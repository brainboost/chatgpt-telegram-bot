"""Tests for the result handler Lambda.

The handler is driven with a fake bot, so these run offline: no AWS, no network,
no Telegram. What they pin is the loop lifetime — every send in one invocation
must run on the same event loop, and the bot's client must be created and
destroyed inside it.

That invariant is not cosmetic. PTB keeps pooled keep-alive connections bound to
the loop that created them, and the handler used to call ``asyncio.run`` per
send, so the second send closed a connection owned by a loop that was already
gone: ``RuntimeError: Event loop is closed``. The failing sends were wrapped in
try/except, so the user simply never received the message.
"""

import asyncio
import base64
import importlib
import json
import zlib
from typing import ClassVar

import pytest
from telegram.error import BadRequest

results = importlib.import_module("lambda.results")
fmt = importlib.import_module("lambda.formatting")


class FakeBot:
    """Stands in for ``telegram.Bot``, recording loop affinity per send.

    ``fail_markdown`` and ``fail_photos`` are class attributes so a test can
    arm them before the handler builds its own bot.
    """

    instances: ClassVar[list[FakeBot]] = []
    fail_markdown: ClassVar[bool] = False
    fail_photos: ClassVar[set[str]] = set()
    fail_shutdown: ClassVar[bool] = False

    def __init__(self, *, token: str) -> None:
        self.token = token
        self.calls: list[str] = []
        self.messages: list[tuple[str, str | None]] = []
        self.photos: list[str] = []
        self.loops: list[asyncio.AbstractEventLoop] = []
        FakeBot.instances.append(self)

    def _touch_loop(self) -> None:
        """Record the running loop, and use it the way httpx does.

        Closing a pooled connection schedules on the loop that created it, which
        raises once that loop is gone — the real failure mode being guarded here.
        """
        loop = asyncio.get_running_loop()
        self.loops.append(loop)
        loop.call_soon(lambda: None)

    async def initialize(self) -> None:
        self.calls.append("initialize")

    async def shutdown(self) -> None:
        self.calls.append("shutdown")
        if FakeBot.fail_shutdown:
            raise BadRequest("failed to close the client")

    async def send_message(self, **kwargs) -> None:
        self._touch_loop()
        # The handler only ever passes MARKDOWN_V2 or None, and the retry is the
        # None one.
        markdown = kwargs.get("parse_mode") is not None
        if markdown and FakeBot.fail_markdown:
            self.calls.append("send_message:markdown-failed")
            raise BadRequest("Can't parse entities: can't find end of bold entity")
        self.calls.append("send_message:markdown" if markdown else "send_message:plain")
        self.messages.append((kwargs["text"], kwargs.get("parse_mode")))

    async def send_photo(self, **kwargs) -> None:
        self._touch_loop()
        self.calls.append("send_photo")
        if kwargs["photo"] in FakeBot.fail_photos:
            raise BadRequest("failed to send photo")
        self.photos.append(kwargs["photo"])


@pytest.fixture(autouse=True)
def fake_bot(monkeypatch):
    FakeBot.instances = []
    FakeBot.fail_markdown = False
    FakeBot.fail_photos = set()
    FakeBot.fail_shutdown = False
    results.__telegram_token.cache_clear()
    monkeypatch.setattr(results, "Bot", FakeBot)
    monkeypatch.setattr(results, "read_ssm_param", lambda **kwargs: "test-token")
    return FakeBot


def _encode(text: str) -> str:
    return base64.b64encode(zlib.compress(text.encode("utf-8"))).decode("ascii")


def _body(message: str, *, kind: str = "text") -> dict:
    return {
        "chat_id": "-100123456789",
        "message_id": 7,
        "type": kind,
        "engine": "gemini",
        "format": "llm",
        "response": _encode(message),
    }


def _event(*messages: str, kind: str = "text") -> dict:
    return {
        "Records": [
            {"Sns": {"Message": json.dumps(_body(message, kind=kind))}}
            for message in messages
        ]
    }


def _run(event: dict) -> FakeBot:
    results.response_handler(event, None)
    (bot,) = FakeBot.instances
    return bot


def test_every_send_of_one_invocation_shares_one_loop():
    """A reply split into parts used to send part 2 on a loop that had closed."""
    bot = _run(_event("x" * 9000))

    assert len(bot.messages) == 3
    assert len({id(loop) for loop in bot.loops}) == 1
    # Closed once the invocation ends, so nothing outlives the loop it belongs to.
    assert all(loop.is_closed() for loop in bot.loops)


def test_the_retry_after_a_failed_markdown_send_actually_sends():
    """The plain retry is a second request on the bot: it needs a live loop."""
    FakeBot.fail_markdown = True

    bot = _run(_event("**bold** text"))

    assert bot.calls == [
        "initialize",
        "send_message:markdown-failed",
        "send_message:plain",
        "shutdown",
    ]
    # The retry carried the raw text, not the escaped source...
    assert bot.messages == [("gemini\n**bold** text", None)]
    # ...and both attempts ran on the same loop, so the second could not trip
    # over a connection the first had left on a closed one.
    assert len({id(loop) for loop in bot.loops}) == 1


def test_the_bot_is_created_and_destroyed_inside_the_invocation():
    bot = _run(_event("hello"))

    assert bot.calls == ["initialize", "send_message:markdown", "shutdown"]
    assert bot.token == "test-token"


def test_a_single_result_is_sent_once():
    bot = _run(_event("hello"))

    assert bot.messages == [
        ("*__gemini__*\nhello", results.constants.ParseMode.MARKDOWN_V2)
    ]


def test_image_results_send_every_photo():
    bot = _run(
        _event(
            "https://example.com/a.png\nhttps://example.com/b.png",
            kind="imagine",
        )
    )

    assert bot.photos == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]
    assert bot.messages == []
    assert len({id(loop) for loop in bot.loops}) == 1


def test_a_line_that_is_not_a_url_is_reported_and_not_sent_as_a_photo():
    bot = _run(_event("not a url\nhttps://example.com/b.png", kind="imagine"))

    assert bot.photos == ["https://example.com/b.png"]
    assert bot.calls.count("send_photo") == 1  # the bad line never reaches the API
    (text, _), = bot.messages
    assert "not a url" in text


def test_a_failing_photo_does_not_stop_the_others():
    """Every image shares the loop, so one failure cannot end the rest."""
    FakeBot.fail_photos = {"https://example.com/a.png"}

    bot = _run(
        _event(
            "https://example.com/a.png\nhttps://example.com/b.png",
            kind="imagine",
        )
    )

    assert bot.photos == ["https://example.com/b.png"]


def test_several_records_are_processed_in_one_invocation():
    bot = _run(_event("one", "two"))

    assert [text for text, _ in bot.messages] == [
        "*__gemini__*\none",
        "*__gemini__*\ntwo",
    ]
    # One bot and one loop for the whole batch, not one per record.
    assert len(FakeBot.instances) == 1
    assert len({id(loop) for loop in bot.loops}) == 1


def test_the_error_message_for_a_bad_line_is_escaped():
    """An unescaped URL used to fail MarkdownV2 with 'can't find end of bold'."""
    bot = _run(_event("not_a_url_with_*_star", kind="imagine"))

    (text, parse_mode), = bot.messages
    assert "\\*" in text  # escaped, so the send cannot fail on it
    assert parse_mode is not None


def test_blank_lines_in_an_image_result_are_ignored():
    """``splitlines`` yields empty entries, which used to reply ``Error: ``."""
    bot = _run(
        _event(
            "https://example.com/a.png\n\nhttps://example.com/b.png",
            kind="imagine",
        )
    )

    assert bot.photos == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]
    assert bot.messages == []


def test_the_token_is_read_from_ssm_once_per_container(monkeypatch):
    """Cached, so a warm container skips the boto3 client and the round trip."""
    lookups = []

    def counting_param(**kwargs):
        lookups.append(kwargs["param_name"])
        return "test-token"

    monkeypatch.setattr(results, "read_ssm_param", counting_param)

    results.response_handler(_event("one"), None)
    results.response_handler(_event("two"), None)

    assert lookups == ["TELEGRAM_TOKEN"]


def test_a_failing_shutdown_does_not_fail_the_invocation():
    """The messages are already out: retrying would deliver them twice."""
    FakeBot.fail_shutdown = True

    bot = _run(_event("hello"))

    assert bot.messages == [("*__gemini__*\nhello", results.constants.ParseMode.MARKDOWN_V2)]
    assert bot.calls[-1] == "shutdown"
