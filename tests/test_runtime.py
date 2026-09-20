"""Regression test for the Lambda loop-lifetime bug (prod: 'Event loop is closed').

The bot Lambda builds one PTB ``Application`` per container but runs
``asyncio.run`` per invocation, i.e. a fresh event loop every time. PTB request
clients and their pooled keep-alive connections belong to the loop that created
them, so a runtime left initialized across invocations reuses a connection whose
transport lives on a closed loop; the next send fails with
``NetworkError: ... RuntimeError('Event loop is closed')`` and the user gets no
reply (PTB logs handler errors instead of raising, so the Lambda still returns
200).

This test drives the real module through a stub Telegram API on localhost with
``Connection: keep-alive``, so the pooled connection survives between
invocations exactly as it does against the real API. The asserted signal is
delivery — did the stub actually receive ``sendMessage`` during that
invocation — not "did the invocation raise".
"""

import asyncio
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from telegram import Update
from telegram.ext import MessageHandler, filters

runtime = importlib.import_module("lambda.runtime")

TOKEN = "123456:TEST"
USER = {
    "id": 42,
    "is_bot": True,
    "first_name": "Test",
    "username": "test_bot",
    "can_join_groups": True,
    "can_read_all_group_messages": False,
    "supports_inline_queries": False,
}
UPDATE_EVENT = {
    "body": json.dumps(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "T"},
                "text": "hello",
            },
        }
    )
}

_current_invocation = {"number": 0}


class StubTelegramApi(BaseHTTPRequestHandler):
    """Minimal Telegram Bot API: getMe + sendMessage, HTTP/1.1 keep-alive."""

    protocol_version = "HTTP/1.1"
    observed: ClassVar[list] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        type(self).observed.append((_current_invocation["number"], self.path))
        if "getMe" in self.path:
            payload = {"ok": True, "result": USER}
        elif "sendMessage" in self.path:
            payload = {
                "ok": True,
                "result": {
                    "message_id": 2,
                    "date": 1,
                    "chat": {"id": 42, "type": "private"},
                    "text": "pong",
                },
            }
        else:
            payload = {"ok": True, "result": True}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


def _build_app(port: int):
    app = runtime.create_application(TOKEN, base_url=f"http://127.0.0.1:{port}/bot")

    async def echo(update: Update, context) -> None:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="pong")

    app.add_handler(MessageHandler(filters.TEXT, echo))
    return app


def test_replies_are_delivered_on_every_warm_invocation():
    StubTelegramApi.observed = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubTelegramApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app = _build_app(server.server_address[1])
    try:
        for number in range(1, 4):
            _current_invocation["number"] = number
            asyncio.run(runtime.process_update_event(UPDATE_EVENT, app))

            delivered = any(
                seen == number and "sendMessage" in path
                for seen, path in StubTelegramApi.observed
            )
            assert delivered, f"invocation {number} did not deliver its reply"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
