"""Result handler Lambda: engine result payloads become Telegram messages.

Decodes the wire content, then delegates all rendering (flavor selection,
escaping, splitting, headers, send fallback) to the formatting module; this
file only owns the AWS/Telegram I/O — the SQS/SNS record loop and the
photo-sending path for image results.
"""

import asyncio
import json
import logging
from urllib.parse import urlparse

from telegram import constants
from telegram.ext import (
    Application,
)

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

telegram_token = read_ssm_param(param_name="TELEGRAM_TOKEN")
app = Application.builder().token(token=telegram_token).build()
bot = app.bot


def response_handler(event, context) -> None:
    """Result SQS processing handler."""

    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        # payload = json.loads(record["body"])
        chat_id = payload["chat_id"]
        message_id = int(payload["message_id"])
        message = decode_message(payload["response"])
        if "imagine" in payload["type"] or "ideogram" in payload["type"]:
            __send_images(chat_id, message_id, message)
        else:
            flavor = resolve_format(payload.get("engine"), payload.get("format"))
            parts = assemble_engine_reply(message, payload["engine"], flavor=flavor)
            logger.info("Sending message in %s parts", len(parts))
            for part in parts:
                formatted_send = __attempt_send(chat_id, message_id, parse_mode=True)
                plain_send = __attempt_send(chat_id, message_id, parse_mode=False)
                send_with_fallback(part, formatted_send, plain_send)


def __attempt_send(chat_id: str, message_id: int, *, parse_mode: bool):
    """Bind one send attempt strategy for ``send_with_fallback``."""

    def attempt(text: str) -> None:
        asyncio.run(
            bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=(
                    constants.ParseMode.MARKDOWN_V2 if parse_mode else None
                ),
                reply_to_message_id=message_id if parse_mode else None,
                disable_notification=True,
                disable_web_page_preview=True,
            )
        )

    return attempt


def __send_images(chat_id: str, message_id: int, message: str) -> None:
    for url in iter(message.splitlines()):
        if not __is_valid_url(url):
            logger.error("chat_id:%s, message_id: %s", chat_id, message_id)
            formatted_send = __attempt_send(chat_id, message_id, parse_mode=True)
            plain_send = __attempt_send(chat_id, message_id, parse_mode=False)
            send_with_fallback(
                reply_part(f"Error: {url}"), formatted_send, plain_send
            )
        try:
            asyncio.run(
                bot.send_photo(
                    chat_id=chat_id,
                    photo=url,
                    reply_to_message_id=message_id,
                    disable_notification=True,
                )
            )
        except Exception as e:
            logger.error("Cannot send photo, url: %s", url, exc_info=e)
            logger.info(message)


def __is_valid_url(url) -> bool:
    parsed_url = urlparse(url)
    return all([parsed_url.scheme, parsed_url.netloc])
