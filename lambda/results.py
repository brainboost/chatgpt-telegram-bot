import asyncio
import json
import logging
from urllib.parse import urlparse

import utils
from telegram import constants
from telegram.error import BadRequest
from telegram.ext import (
    Application,
)
from user_config import UserConfig

logging.basicConfig()
logging.getLogger().setLevel("INFO")

user_config = UserConfig()

telegram_token = utils.read_ssm_param(param_name="TELEGRAM_TOKEN")
app = Application.builder().token(token=telegram_token).build()
bot = app.bot


def response_handler(event, context) -> None:
    logging.info(event)
    for record in event["Records"]:
        payload = json.loads(record["body"])
        logging.info(payload)
        message_id = payload["message_id"]
        chat_id = payload["chat_id"]
        message = utils.decode_message(payload["response"])
        if "images" in payload["type"]:
            caption = payload["text"]
            __send_images(chat_id, message_id, message, caption)
        else:
            text = f"*__{payload['engine']}__*: {message}"
            __send_text(chat_id, message_id, text)


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


def __send_images(chat_id: str, message_id: str, message: str, caption: str) -> None:
    logging.info(message)
    for url in iter(message.splitlines()):
        if not __is_valid_url(url):
            __send_text(chat_id, message_id, f"Error: {url}")
        try:
            asyncio.get_event_loop().run_until_complete(
                bot.send_photo(
                    chat_id=chat_id,
                    photo=url,
                    caption=caption,
                    reply_to_message_id=message_id,
                    disable_notification=True,
                )
            )
        except Exception as e:
            logging.error(f"Cannot send message, error: {e}, \nPayload: {url}")


def __is_valid_url(url) -> bool:
    parsed_url = urlparse(url)
    return all([parsed_url.scheme, parsed_url.netloc])
