import asyncio
import json
import logging
from urllib.parse import urlparse

from telegram import constants
from telegram.error import BadRequest
from telegram.ext import (
    Application,
)

from .utils import decode_message, read_ssm_param, split_long_message

MAX_MESSAGE_SIZE = 4060

logging.basicConfig()
logging.getLogger().setLevel("INFO")

telegram_token = read_ssm_param(param_name="TELEGRAM_TOKEN")
app = Application.builder().token(token=telegram_token).build()
bot = app.bot


def response_handler(event, context) -> None:
    """Result SQS processing handler."""

    for record in event["Records"]:
        payload = json.loads(record["body"])
        chat_id = payload["chat_id"]
        message_id = payload["message_id"]
        message = decode_message(payload["response"])
        if "imagine" in payload["type"] or "ideogram" in payload["type"]:
            __send_images(chat_id, message_id, message)
        else:
            parts = split_long_message(
                message, f"*__{payload['engine']}__*", MAX_MESSAGE_SIZE
            )
            logging.info(f"Sending message in {parts.len()} parts")
            for part in parts:
                __send_text(chat_id, message_id, part)


def __send_text(chat_id: str, message_id: str, text: str) -> None:
    try:
        asyncio.get_event_loop().run_until_complete(
            bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_to_message_id=message_id,
                disable_notification=True,
                disable_web_page_preview=True,
            )
        )
    except BadRequest as br:
        logging.error(br)
        # send without reply
        asyncio.get_event_loop().run_until_complete(
            bot.send_message(
                chat_id=chat_id,
                text=text,
                disable_notification=True,
                disable_web_page_preview=True,
            )
        )
    except Exception as e:
        logging.error(f"Cannot send message, error: {e}, \nPayload: {text}")
        # send plaintext
        asyncio.get_event_loop().run_until_complete(
            bot.send_message(
                chat_id=chat_id,
                text=text.replace("__", " "),
                reply_to_message_id=message_id,
                disable_notification=True,
                disable_web_page_preview=True,
            )
        )


def __send_images(chat_id: str, message_id: str, message: str) -> None:
    for url in iter(message.splitlines()):
        if not __is_valid_url(url):
            logging.error(f"chat_id:{chat_id}, message_id: {message_id}")
            __send_text(chat_id, message_id, f"Error: {url}")
        try:
            asyncio.get_event_loop().run_until_complete(
                bot.send_photo(
                    chat_id=chat_id,
                    photo=url,
                    reply_to_message_id=message_id,
                    disable_notification=True,
                )
            )
        except Exception as e:
            logging.error(f"Cannot send message, error: {e}, \nPayload: {url}")
            logging.info(message)


def __is_valid_url(url) -> bool:
    parsed_url = urlparse(url)
    return all([parsed_url.scheme, parsed_url.netloc])
