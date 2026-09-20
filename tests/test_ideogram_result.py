"""Tests for the Ideogram result poller (offline: no AWS, no HTTP).

Covers the two properties that matter operationally: the poll chain is bounded
(a stuck generation tells the user instead of polling forever) and the queue
message never carries credentials.
"""

import importlib
import json

import pytest

result_mod = importlib.import_module("engines.ideogram_result")
PollOutcome = result_mod.PollOutcome

METADATA_READY = {
    "resolution": 1024,
    "responses": [{"response_id": "a"}, {"response_id": "b"}],
}
METADATA_PENDING = {"resolution": 0, "responses": []}
METADATA_NO_RESOLUTION = {"responses": []}


def _payload(attempt=None):
    payload = {
        "type": "ideogram",
        "text": "a cat",
        "user_id": 42,
        "chat_id": -1,
        "message_id": 7,
        "result_id": "res-1",
        "queue_url": "https://sqs.example/queue",
    }
    if attempt is not None:
        payload["attempt"] = attempt
    return payload


def test_ready_result_returns_image_urls():
    outcome = result_mod.evaluate_poll(_payload(), METADATA_READY)

    assert outcome.gave_up is False
    assert outcome.retry_in is None
    assert outcome.urls.splitlines() == [
        "https://ideogram.ai/api/images/direct/a",
        "https://ideogram.ai/api/images/direct/b",
    ]


def test_missing_resolution_is_not_ready():
    assert result_mod.is_ready(METADATA_NO_RESOLUTION) is False
    assert result_mod.is_ready({}) is False


def test_low_resolution_is_not_ready():
    assert result_mod.is_ready({"resolution": 512}) is False


def test_ready_result_never_requeues():
    def schedule(*args, **kwargs):
        raise AssertionError("a ready result must not be re-queued")

    outcome = result_mod.poll_images(
        _payload(),
        fetch=lambda payload: METADATA_READY,
        schedule=schedule,
    )

    assert outcome.urls


def test_pending_result_requeues_with_incremented_attempt_and_delay():
    scheduled = []

    def schedule(payload, *, attempt, delay_seconds):
        scheduled.append((payload["result_id"], attempt, delay_seconds))

    outcome = result_mod.poll_images(
        _payload(),
        fetch=lambda payload: METADATA_PENDING,
        schedule=schedule,
    )

    assert scheduled == [("res-1", 1, 5)]
    assert outcome.retry_in == 5
    assert outcome.attempt == 1


def test_backoff_grows_and_is_capped_per_delay_table():
    expected = [
        (0, 5),
        (1, 5),
        (2, 10),
        (3, 10),
        (4, 15),
        (5, 15),
        (6, 20),
    ]
    for attempt, delay in expected:
        outcome = result_mod.evaluate_poll(_payload(attempt), METADATA_PENDING)
        assert outcome.retry_in == delay, f"attempt {attempt}"
        assert outcome.attempt == attempt + 1
        assert outcome.gave_up is False


def test_polling_stops_after_the_budget_and_does_not_requeue():
    attempts = []
    payload = _payload(result_mod.MAX_POLLS - 1)

    def schedule(*args, **kwargs):
        attempts.append(kwargs)
        raise AssertionError("the last attempt must not be re-queued")

    outcome = result_mod.poll_images(
        payload, fetch=lambda payload: METADATA_PENDING, schedule=schedule
    )

    assert outcome.gave_up is True
    assert outcome.attempt == result_mod.MAX_POLLS
    assert attempts == []


def test_delay_table_covers_every_retry_before_the_budget():
    # Every attempt that is allowed to re-queue must have a delay entry.
    for attempt in range(result_mod.MAX_POLLS - 1):
        assert result_mod.retry_delay(attempt) in result_mod.RETRY_DELAYS


class _Context:
    aws_request_id = "request-1"


def _event(payload):
    return {"Records": [{"body": json.dumps(payload)}]}


def test_handler_publishes_ready_images(monkeypatch):
    published = []
    monkeypatch.setattr(
        result_mod,
        "poll_images",
        lambda payload, **kwargs: PollOutcome(urls="https://img/1", attempt=2),
    )
    monkeypatch.setattr(
        result_mod, "publish_result", lambda payload, label, text, **kw: published.append((payload, label, text, kw))
    )

    result_mod.sqs_handler(_event(_payload()), _Context())

    assert published == [(_payload(), "ideogram", "https://img/1", {})]


def test_handler_tells_the_user_when_it_gives_up(monkeypatch):
    published = []
    monkeypatch.setattr(
        result_mod,
        "poll_images",
        lambda payload, **kwargs: PollOutcome(attempt=result_mod.MAX_POLLS, gave_up=True),
    )
    monkeypatch.setattr(
        result_mod, "publish_result", lambda payload, label, text, **kw: published.append((payload, label, text, kw))
    )

    result_mod.sqs_handler(_event(_payload()), _Context())

    assert len(published) == 1
    payload, label, text, kwargs = published[0]
    assert label == "ideogram"
    assert text == result_mod.TOO_LONG_MESSAGE
    assert kwargs == {"format": "plain"}
    # published as text, not as an image result (else the sender would reply
    # "Error: <sentence>")
    assert payload["type"] == "text"
    assert payload["result_id"] == "res-1"


def test_handler_does_nothing_while_still_pending(monkeypatch):
    published = []
    monkeypatch.setattr(
        result_mod,
        "poll_images",
        lambda payload, **kwargs: PollOutcome(retry_in=5, attempt=1),
    )
    monkeypatch.setattr(
        result_mod, "publish_result", lambda *args, **kwargs: published.append(args)
    )

    result_mod.sqs_handler(_event(_payload()), _Context())

    assert published == []


def test_failed_poll_raises_for_the_dlq():
    with pytest.raises(result_mod.IdeogramError, match="result_id"):
        result_mod.fetch_metadata({"result_id": ""})
