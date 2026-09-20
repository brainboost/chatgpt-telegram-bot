"""How the bot Lambda hosts python-telegram-bot.

Interface:

- ``create_application(token, *, base_url=None)`` builds the ``Application``
  with the exact options the Lambda needs (concurrent updates, HTTP/1.1 for
  both the API and get-updates connections).
- ``process_update_event(event, app)`` processes one API Gateway webhook event.

Loop lifetime is the load-bearing part: PTB keeps resources that belong to the
event loop that created them — request clients and, crucially, their pooled
keep-alive connections. The Lambda runs ``asyncio.run`` per invocation, so a
new loop every time; anything left over from the previous loop is unusable and
fails with ``NetworkError: ... RuntimeError('Event loop is closed')`` on the
first reuse. Initializing and shutting the runtime down inside the same
invocation keeps that invariant true.
"""

import json
import logging

from telegram import Update
from telegram.ext import Application

logger = logging.getLogger(__name__)


def create_application(token: str, *, base_url: str | None = None) -> Application:
    """Build the bot Application; ``base_url`` overrides the API host (tests)."""
    builder = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .http_version("1.1")
        .get_updates_http_version("1.1")
    )
    if base_url is not None:
        builder = builder.base_url(base_url)
    return builder.build()


async def process_update_event(event: dict, app: Application) -> None:
    """Process one API Gateway webhook event.

    ``event["body"]`` is the raw Telegram update JSON.

    The runtime is initialized and shut down **inside this invocation**: PTB's
    request clients (and the pooled connections under them) are bound to the
    loop that created them, and this Lambda gets a new loop per invocation, so
    nothing may survive into the next one. ``Application.shutdown`` closes the
    clients; the next ``initialize`` rebuilds them in the new loop (it also
    re-runs ``getMe``, one extra API call per invocation, which is the price of
    the invariant).
    """
    await app.initialize()
    try:
        update = Update.de_json(json.loads(event["body"]), app.bot)
        await app.process_update(update)
    finally:
        await app.shutdown()
