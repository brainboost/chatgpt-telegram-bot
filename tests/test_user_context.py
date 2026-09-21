"""Tests for the deepened conversation state (candidate 3).

The store seam is exercised through MemoryContextStore — no AWS — and the
ollama adapter's message builder is tested as a pure function (ollama.py is
import-safe offline). The Gemini engine no longer replays a transcript: its web
backend keeps history server-side, so what it stores is the engine-scoped
``session`` blob asserted below.
"""

import time

from engines.ollama import _build_messages
from engines.user_context import (
    MAX_TURNS,
    TURN_CHAR_CAP,
    MemoryContextStore,
    UserContext,
)


def _store():
    return MemoryContextStore()


def _context(store, user_id="42_-100", engine="gemini"):
    return UserContext(
        user_id=user_id,
        engine_id=engine,
        request_id="req-1",
        username="tester",
        store=store,
    )


def test_empty_memory_when_no_row_exists():
    store = _store()
    context = _context(store)
    assert context.turns == []


def test_add_and_persist_roundtrip():
    store = _store()
    context = _context(store)
    context.add_turn("hello", "hi there")
    context.persist()

    reloaded = _context(store)
    assert reloaded.turns == [{"request": "hello", "response": "hi there"}]


def test_history_loaded_at_construction():
    store = _store()
    first = _context(store)
    first.add_turn("q1", "a1")
    first.persist()

    second = _context(store)
    assert second.turns == [{"request": "q1", "response": "a1"}]


def test_memory_is_capped_to_max_turns():
    store = _store()
    context = _context(store)
    for i in range(MAX_TURNS + 4):
        context.add_turn(f"req{i}", f"resp{i}")
    context.persist()

    reloaded = _context(store)
    assert len(reloaded.turns) == MAX_TURNS
    # oldest exchanges were dropped: first survivor is req4 for MAX_TURNS=8
    assert reloaded.turns[0]["request"] == "req4"
    assert reloaded.turns[-1]["request"] == f"req{MAX_TURNS + 3}"


def test_turn_sides_are_trimmed_to_char_cap():
    store = _store()
    context = _context(store)
    context.add_turn("x" * (TURN_CHAR_CAP * 2), "y" * (TURN_CHAR_CAP + 100))
    context.persist()

    stored = _context(store).turns[0]
    assert len(stored["request"]) == TURN_CHAR_CAP
    assert len(stored["response"]) == TURN_CHAR_CAP


def test_persist_writes_expiry_for_ttl():
    store = _store()
    context = _context(store)
    context.add_turn("q", "a")
    context.persist()

    row = store.load("42_-100", "gemini")
    assert row is not None
    assert row["exp"] > int(time.time())  # ~60 days ahead, far in the future


def test_reset_deletes_the_row_and_clears_memory():
    store = _store()
    context = _context(store)
    context.add_turn("q", "a")
    context.persist()

    context.reset()

    assert store.load("42_-100", "gemini") is None
    assert context.turns == []
    assert _context(store).turns == []


def test_session_state_is_empty_without_a_row():
    assert _context(_store()).session == {}


def test_session_state_roundtrips_through_the_row():
    store = _store()
    context = _context(store)
    context.set_session({"cid": "c_1", "rid": "r_2", "rcid": "rc_3", "context": "tok"})
    context.persist()

    assert _context(store).session == {
        "cid": "c_1",
        "rid": "r_2",
        "rcid": "rc_3",
        "context": "tok",
    }


def test_session_and_turns_share_one_row():
    store = _store()
    context = _context(store)
    context.add_turn("q1", "a1")
    context.set_session({"cid": "c_1"})
    context.persist()

    reloaded = _context(store)
    assert reloaded.turns == [{"request": "q1", "response": "a1"}]
    assert reloaded.session == {"cid": "c_1"}


def test_an_empty_session_is_not_written():
    store = _store()
    context = _context(store)
    context.add_turn("q1", "a1")
    context.persist()

    assert "session" not in store.load("42_-100", "gemini")


def test_reset_clears_session_state():
    store = _store()
    context = _context(store)
    context.set_session({"cid": "c_1"})
    context.persist()

    context.reset()

    assert context.session == {}
    assert _context(store).session == {}


def test_a_legacy_row_without_session_reads_as_empty():
    store = _store()
    store.save("42_-100", "gemini", {"turns": [], "exp": 1})

    assert _context(store).session == {}


def test_ollama_messages_alternate_history_then_new_text():
    turns = [
        {"request": "q1", "response": "a1"},
        {"request": "q2", "response": "a2"},
    ]
    assert _build_messages("q3", turns) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
    ]


def test_ollama_messages_without_history_send_only_the_text():
    assert _build_messages("q1", []) == [{"role": "user", "content": "q1"}]
