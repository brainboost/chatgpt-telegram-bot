"""Tests for the engine session runtime (candidate 2/3 deepening).

These drive ``run_engine_event`` through its interface — a stub responder, an
injected session factory and a recording publisher — so the whole
intake → session → answer → save → publish pipeline is covered without AWS.
"""

import base64
import importlib
import zlib

import pytest

import providers
from engines.session import EngineResponder, run_engine_event


class FakeContext:
    """Records the session lifecycle calls the runtime makes."""

    def __init__(self, turns=None):
        self.reset_called = False
        self.turns = list(turns or [])
        self.persisted = 0

    def add_turn(self, request, response):
        self.turns.append({"request": request, "response": response})

    def persist(self):
        self.persisted += 1

    def reset(self):
        self.reset_called = True


class StubResponder(EngineResponder):
    def __init__(self, answer_fn=None, label="stub", wants_session=False,
                 reply_on_error=False, format=None, fails_over=None):
        self.label = label
        self.wants_session = wants_session
        self.reply_on_error = reply_on_error
        if format is not None:
            self.format = format
        if fails_over:
            self.fails_over = True
        self._answer_fn = answer_fn
        self.calls = 0

    def answer(self, payload, context):
        self.calls += 1
        if self._answer_fn is not None:
            return self._answer_fn(payload, context)
        return "stub answer"


def _text_payload(text="hello engines"):
    return {
        "type": "text",
        "user_id": 42,
        "chat_id": -100123456789,
        "username": "tester",
        "message_id": 7,
        "update_id": 9,
        "text": text,
        "config": {"engines": ["gemini"]},
    }


def _decode_response(payload):
    return zlib.decompress(
        base64.b64decode(payload["response"].encode("ascii"))
    ).decode("utf-8")


def _run(payload, responder, context=None, **kwargs):
    context = context or FakeContext()

    def factory(payload, request_id, engine_label):
        factory.payload = payload
        factory.request_id = request_id
        factory.engine_label = engine_label
        return context

    published = []
    run_engine_event(
        payload,
        request_id="req-1",
        responder=responder,
        publish=published.append,
        context_factory=factory,
        **kwargs,
    )
    return context, published, factory


def test_single_result_published_and_saved():
    responder = StubResponder(wants_session=True)
    context, published, factory = _run(_text_payload(), responder)

    assert len(published) == 1
    result = published[0]
    assert result["engine"] == "stub"
    assert _decode_response(result) == "stub answer"
    # wire fields ride along unchanged
    assert result["user_id"] == 42
    assert result["chat_id"] == -100123456789
    # session built with engine label + composite identity source
    assert factory.engine_label == "stub"
    assert factory.request_id == "req-1"
    # turn appended then persisted once, before publish
    assert context.persisted == 1
    assert context.turns == [{"request": "hello engines", "response": "stub answer"}]


def test_a_declared_format_still_wins_over_the_shared_default():
    responder = StubResponder(wants_session=True, format="plain")

    _, published, _ = _run(_text_payload(), responder)

    assert published[0]["format"] == "plain"


def test_history_is_visible_to_the_responder():
    loaded_turns = [
        {"request": "earlier q", "response": "earlier a"},
    ]
    seen = {}

    def capture(payload, ctx):
        seen["turns"] = list(ctx.turns)
        return "fresh answer"

    responder = StubResponder(wants_session=True, answer_fn=capture)
    context = FakeContext(turns=loaded_turns)
    context, _, _ = _run(_text_payload("fresh q"), responder, context=context)

    assert seen["turns"] == loaded_turns  # history loaded before the provider call
    assert context.turns == loaded_turns + [
        {"request": "fresh q", "response": "fresh answer"}
    ]


def test_multi_result_per_label_published():
    responder = StubResponder(
        wants_session=False,
        answer_fn=lambda payload, ctx: [
            ("EN-GB", "hello"),
            ("PL", "witaj"),
        ],
    )
    context, published, factory = _run(_text_payload(), responder)

    assert [p["engine"] for p in published] == ["EN-GB", "PL"]
    assert [_decode_response(p) for p in published] == ["hello", "witaj"]
    # translate-style responders keep no session: nothing saved or persisted
    assert not hasattr(factory, "engine_label")
    assert context.turns == []
    assert context.persisted == 0


def test_responder_format_is_stamped_on_the_wire():
    responder = StubResponder(wants_session=True, format="plain")
    _, published, _ = _run(_text_payload(), responder)

    assert published[0]["format"] == "plain"


def test_default_responder_format_is_the_shared_content_flavor():
    """The regression that made a whole renderer unreachable.

    A declared format rides the wire and *overrides* the sender's own default,
    so a responder that declares nothing still has to name the flavor the sender
    would have picked. A hardcoded ``"markdown"`` here pinned every chat engine
    to the legacy renderer, and the sender's new default never ran.
    """
    fmt = importlib.import_module("lambda.formatting")
    responder = StubResponder(wants_session=True)  # declares no format at all

    _, published, _ = _run(_text_payload(), responder)

    assert published[0]["format"] == fmt.DEFAULT_FLAVOR
    assert fmt.DEFAULT_FLAVOR == providers.DEFAULT_CONTENT_FLAVOR


def _failing_responder(label, **kwargs):
    def boom(payload, ctx):
        raise ValueError("provider down")

    return StubResponder(
        label=label, answer_fn=boom, wants_session=True, fails_over=True, **kwargs
    )


def _republish_recorder(republished):
    def record(payload, next_provider_id):
        republished.append((payload, next_provider_id))

    return record


def test_fails_over_republishes_to_the_next_provider():
    responder = _failing_responder("gemini")
    republished = []
    context, published, _ = _run(
        _text_payload(), responder, republish=_republish_recorder(republished)
    )

    assert republished == [(_text_payload(), "qwen")]  # chain gemini -> qwen
    assert published == []
    # nothing saved: the failed provider keeps no turn
    assert context.persisted == 0
    assert context.turns == []


def test_fails_over_advances_from_the_middle_of_the_chain():
    responder = _failing_responder("qwen")
    republished = []
    _, published, _ = _run(
        _text_payload(), responder, republish=_republish_recorder(republished)
    )

    assert [next_id for _, next_id in republished] == ["llama"]
    assert published == []


def test_fails_over_at_chain_tail_replies_an_error():
    responder = _failing_responder("llama")
    republished = []
    context, published, _ = _run(
        _text_payload(), responder, republish=_republish_recorder(republished)
    )

    assert republished == []
    assert len(published) == 1
    result = published[0]
    assert result["engine"] == "llama"
    assert result["format"] == "plain"
    assert _decode_response(result).startswith("All chat providers failed to answer")
    # the outage is not recorded as a conversation turn
    assert context.persisted == 0
    assert context.turns == []


def test_non_failover_responders_keep_the_reply_on_error_policy():
    def boom(payload, ctx):
        raise ValueError("boom")

    responder = StubResponder(
        wants_session=True,
        reply_on_error=True,
        answer_fn=boom,
    )
    republished = []
    context, published, _ = _run(
        _text_payload(), responder, republish=republished.append
    )

    assert republished == []
    assert len(published) == 1
    assert _decode_response(published[0]) == "boom"
    assert context.persisted == 1


def test_ping_publishes_pong_without_history():
    responder = StubResponder(wants_session=True)
    context, published, _ = _run(_text_payload("/ping"), responder)

    assert responder.calls == 0  # provider never invoked
    assert len(published) == 1
    assert published[0]["engine"] == "stub"
    assert _decode_response(published[0]) == "pong"
    # deliberate behavior change: pong is not saved to conversation history
    assert context.turns == []
    assert context.persisted == 0


def test_command_reset_resets_session_and_publishes_nothing():
    responder = StubResponder(wants_session=True)
    payload = _text_payload()
    payload["type"] = "command"
    payload["text"] = "/reset"

    context, published, _ = _run(payload, responder)

    assert context.reset_called
    assert published == []
    assert responder.calls == 0


def test_unknown_command_is_a_noop():
    responder = StubResponder(wants_session=True)
    payload = _text_payload()
    payload["type"] = "command"
    payload["text"] = "/bogus"

    context, published, _ = _run(payload, responder)

    assert not context.reset_called
    assert published == []
    assert responder.calls == 0


def test_reply_on_error_encodes_error_text():
    def boom(payload, ctx):
        raise ValueError("engine exploded")

    responder = StubResponder(
        wants_session=True, reply_on_error=True, answer_fn=boom
    )
    context, published, _ = _run(_text_payload(), responder)

    assert len(published) == 1
    assert _decode_response(published[0]) == "engine exploded"
    # error replies are stored like normal ones
    assert context.persisted == 1
    assert context.turns == [{"request": "hello engines", "response": "engine exploded"}]


def test_raise_on_error_reaches_caller():
    def boom(payload, ctx):
        raise ValueError("engine exploded")

    responder = StubResponder(
        wants_session=True, reply_on_error=False, answer_fn=boom
    )
    with pytest.raises(Exception, match="engine exploded"):
        _run(_text_payload(), responder)
