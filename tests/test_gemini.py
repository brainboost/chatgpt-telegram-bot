"""Tests for the Gemini engine responder.

The responder is exercised against its provider-I/O seam (``gemini.generate``),
so these run offline: no network, no credentials, no model.
"""

import pytest

from engines import gemini
from engines.gemini_web import (
    ConversationState,
    CredentialsRejectedError,
    GeminiError,
    TemporarilyBlockedError,
    TurnResult,
    UsageLimitError,
)
from engines.session import run_engine_event
from engines.user_context import MemoryContextStore, UserContext


def _context(store):
    return UserContext(
        user_id="42_-100",
        engine_id="gemini",
        request_id="req-1",
        username="tester",
        store=store,
    )


def _reply(state, text="the answer"):
    def generate(prompt, incoming):
        return TurnResult(text=text, state=state if not incoming.cid else incoming)

    return generate


def test_responder_returns_the_answer(monkeypatch):
    monkeypatch.setattr(
        gemini, "generate", _reply(ConversationState(cid="c_1", rid="r_2", rcid="rc_3"))
    )

    answer = gemini.GeminiResponder().answer({"text": "hi"}, _context(MemoryContextStore()))

    assert answer == "the answer"


def test_responder_persists_the_new_thread_state(monkeypatch):
    monkeypatch.setattr(
        gemini,
        "generate",
        _reply(
            ConversationState(cid="c_1", rid="r_2", rcid="rc_3", context="ctx")
        ),
    )
    store = MemoryContextStore()
    context = _context(store)

    answer = gemini.GeminiResponder().answer({"text": "hi"}, context)
    context.add_turn("hi", answer)
    context.persist()

    assert _context(store).session == {
        "cid": "c_1",
        "rid": "r_2",
        "rcid": "rc_3",
        "context": "ctx",
    }


def test_responder_resumes_the_stored_conversation(monkeypatch):
    seen = {}

    def generate(prompt, state):
        seen["state"] = state
        return TurnResult(text="ok", state=state)

    monkeypatch.setattr(gemini, "generate", generate)
    context = _context(MemoryContextStore())
    context.set_session({"cid": "c_9", "rid": "r_9", "rcid": "rc_9", "context": "ctx"})

    gemini.GeminiResponder().answer({"text": "more"}, context)

    assert seen["state"] == ConversationState(
        cid="c_9", rid="r_9", rcid="rc_9", context="ctx"
    )


def test_responder_starts_a_new_conversation_without_stored_state(monkeypatch):
    seen = {}

    def generate(prompt, state):
        seen["state"] = state
        return TurnResult(text="ok", state=state)

    monkeypatch.setattr(gemini, "generate", generate)

    gemini.GeminiResponder().answer({"text": "hi"}, _context(MemoryContextStore()))

    assert seen["state"].is_new()


def test_responder_works_without_a_context(monkeypatch):
    monkeypatch.setattr(gemini, "generate", _reply(ConversationState()))

    assert gemini.GeminiResponder().answer({"text": "hi"}, None) == "the answer"


def test_responder_declares_the_session_and_failover_contract():
    responder = gemini.GeminiResponder()

    assert responder.label == "gemini"
    assert responder.wants_session is True
    assert responder.reply_on_error is False
    assert responder.fails_over is True


def test_a_quota_error_fails_over_instead_of_reaching_the_user(monkeypatch):
    def exhausted(prompt, state):
        raise UsageLimitError("quota spent")

    monkeypatch.setattr(gemini, "generate", exhausted)
    published = []
    republished = []

    run_engine_event(
        {"text": "hi", "user_id": "42", "chat_id": "-100"},
        "req-1",
        gemini._RESPONDER,
        publish=published.append,
        context_factory=lambda *args, **kwargs: None,
        republish=lambda payload, provider: republished.append(provider),
        chain=lambda label: "qwen",
    )

    assert republished == ["qwen"]
    assert published == []


def test_the_chain_tail_reports_the_error_to_the_user(monkeypatch):
    def exhausted(prompt, state):
        raise UsageLimitError("quota spent")

    monkeypatch.setattr(gemini, "generate", exhausted)
    published = []

    run_engine_event(
        {"text": "hi", "user_id": "42", "chat_id": "-100"},
        "req-1",
        gemini._RESPONDER,
        publish=published.append,
        context_factory=lambda *args, **kwargs: None,
        republish=lambda payload, provider: None,
        chain=lambda label: None,
    )

    assert len(published) == 1
    assert published[0]["format"] == "plain"
    assert published[0]["engine"] == "gemini"


def test_a_failing_followup_is_retried_as_a_new_conversation(monkeypatch):
    """A thread Google will not continue must not stay stored forever."""
    calls = []

    def generate(prompt, state):
        calls.append(state.cid)
        if not state.is_new():
            raise GeminiError("1097")
        return TurnResult(text="fresh answer", state=ConversationState(cid="c_new"))

    monkeypatch.setattr(gemini, "generate", generate)
    store = MemoryContextStore()
    context = _context(store)
    context.set_session({"cid": "c_dead", "rid": "r_1"})

    answer = gemini.GeminiResponder().answer({"text": "more"}, context)
    # What the session runtime does after a successful answer.
    context.add_turn("more", answer)
    context.persist()

    assert answer == "fresh answer"
    assert calls == ["c_dead", ""]
    assert context.session["cid"] == "c_new"
    assert _context(store).session["cid"] == "c_new"


def test_the_dead_thread_is_cleared_even_if_the_retry_also_fails(monkeypatch):
    def generate(prompt, state):
        raise GeminiError("always fails")

    monkeypatch.setattr(gemini, "generate", generate)
    store = MemoryContextStore()
    context = _context(store)
    context.set_session({"cid": "c_dead", "rid": "r_1"})
    context.persist()

    with pytest.raises(GeminiError):
        gemini.GeminiResponder().answer({"text": "more"}, context)

    assert _context(store).session == {}


@pytest.mark.parametrize(
    "error", [UsageLimitError, TemporarilyBlockedError, CredentialsRejectedError]
)
def test_account_wide_errors_are_not_retried_as_a_new_conversation(
    monkeypatch, error
):
    """Retrying cannot help when the whole account is limited or blocked."""
    calls = []

    def generate(prompt, state):
        calls.append(state.cid)
        raise error("account wide")

    monkeypatch.setattr(gemini, "generate", generate)
    context = _context(MemoryContextStore())
    context.set_session({"cid": "c_1", "rid": "r_1"})

    with pytest.raises(error):
        gemini.GeminiResponder().answer({"text": "more"}, context)

    assert calls == ["c_1"]  # one attempt, no pointless retry


def test_a_failing_first_turn_is_not_retried(monkeypatch):
    calls = []

    def generate(prompt, state):
        calls.append(state.cid)
        raise GeminiError("nope")

    monkeypatch.setattr(gemini, "generate", generate)

    with pytest.raises(GeminiError):
        gemini.GeminiResponder().answer({"text": "hi"}, _context(MemoryContextStore()))

    assert calls == [""]
