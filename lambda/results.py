"""Result handler Lambda: engine result payloads become Telegram messages.

Decodes the wire content, then delegates all rendering (flavor selection,
escaping, splitting, headers, send fallback) to the formatting module; this file
only owns the AWS/Telegram I/O — the SQS/SNS record loop and the photo-sending
path for image results.

Every send in one invocation shares a **single** event loop, and the bot's HTTP
client is created and destroyed inside that loop. PTB keeps pooled keep-alive
connections bound to the loop that created them, so the previous shape here — an
``asyncio.run`` per send — left the second send closing a connection owned by a
loop that was already gone: ``RuntimeError: Event loop is closed``. Because each
send was wrapped in its own try/except, that surfaced only as a log line while
the message never arrived: it broke the retry after a failed MarkdownV2 send, and
every image after the first in an image result. ``runtime.py`` documents the same
invariant for the webhook handler.
"""

import asyncio
import functools
import json
import logging
from urllib.parse import urlparse

from telegram import Bot, constants

from .formatting import (
    assemble_engine_reply,
    reply_part,
    resolve_format,
    send_with_fallback,
)
from .utils import decode_message, read_ssm_param

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)


def response_handler(event, context) -> None:
    """Result SQS processing handler.

    One loop, one bot, and every send of the invocation awaited inside it.
    """
    asyncio.run(__process_records(event))


@functools.cache
def __telegram_token() -> str:
    """The bot token, read from SSM once per container.

    Resolved at call time rather than at import, so this module stays importable
    (which is what makes it testable), and cached so a warm container does not pay
    for a boto3 client and an SSM round trip on every invocation. A failed lookup
    is not cached — ``functools.cache`` only stores returns.
    """
    return read_ssm_param(param_name="TELEGRAM_TOKEN")


async def __process_records(event: dict) -> None:
    """Build the bot for this invocation, send everything, shut it down."""
    bot = Bot(token=__telegram_token())
    # initialize() builds the HTTP client inside this loop and fetches getMe;
    # shutdown() closes it, so nothing outlives the loop it belongs to.
    await bot.initialize()
    try:
        for record in event["Records"]:
            await __process_record(bot, record)
    finally:
        try:
            await bot.shutdown()
        except Exception as e:
            # Every message has already been sent by now, so letting this fail
            # would only make the asynchronous invocation retry and deliver the
            # whole reply a second time.
            logger.error("Ignoring a failure while shutting the bot down", exc_info=e)


async def __process_record(bot: Bot, record: dict) -> None:
    payload = json.loads(record["Sns"]["Message"])
    chat_id = payload["chat_id"]
    message_id = int(payload["message_id"])
    message = decode_message(payload["response"])
    if "imagine" in payload["type"] or "ideogram" in payload["type"]:
        await __send_images(bot, chat_id, message_id, message)
        return
    flavor = resolve_format(payload.get("engine"), payload.get("format"))
    parts = assemble_engine_reply(message, payload["engine"], flavor=flavor)
    logger.info("Sending message in %s parts", len(parts))
    for part in parts:
        await send_with_fallback(
            part,
            __attempt_send(bot, chat_id, message_id, parse_mode=True),
            __attempt_send(bot, chat_id, message_id, parse_mode=False),
        )


def __attempt_send(bot: Bot, chat_id: str, message_id: int, *, parse_mode: bool):
    """Bind one send attempt strategy for ``send_with_fallback``."""

    async def attempt(text: str) -> None:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=constants.ParseMode.MARKDOWN_V2 if parse_mode else None,
            reply_to_message_id=message_id if parse_mode else None,
            disable_notification=True,
            disable_web_page_preview=True,
        )

    return attempt


async def __send_images(
    bot: Bot, chat_id: str, message_id: int, message: str
) -> None:
    """Send each URL in an image result as a photo.

    A line that is not a URL is reported and skipped, and a blank line is skipped
    silently: the result is built from ``splitlines()``, so an empty or trailing
    line would otherwise reach the user as an ``Error:`` reply with nothing in it.
    """
    for url in message.splitlines():
        if not url.strip():
            continue
        if not __is_valid_url(url):
            logger.error(
                "Image result carried a line that is not a URL "
                "(chat_id=%s, message_id=%s)",
                chat_id,
                message_id,
            )
            await send_with_fallback(
                reply_part(f"Error: {url}"),
                __attempt_send(bot, chat_id, message_id, parse_mode=True),
                __attempt_send(bot, chat_id, message_id, parse_mode=False),
            )
            continue
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=url,
                reply_to_message_id=message_id,
                disable_notification=True,
            )
        except Exception as e:
            logger.error("Cannot send photo, url: %s", url, exc_info=e)


def __is_valid_url(url) -> bool:
    parsed_url = urlparse(url)
    return all([parsed_url.scheme, parsed_url.netloc])
