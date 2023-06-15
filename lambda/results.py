import asyncio
import json
import logging

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
        if "images" in payload["type"]:
            __send_images(payload)
        else:
            __sent_text(payload)


def __sent_text(payload: list) -> None:
    message_id = payload["message_id"]
    message = utils.decode_message(payload["response"])
    try:
        asyncio.get_event_loop().run_until_complete(
            bot.send_message(
                chat_id=payload["chat_id"],
                text=f"*__{payload['engine']}__*: {message}",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_to_message_id=message_id,
                disable_notification=True,
                disable_web_page_preview=True,
            )
        )
    except BadRequest as br:
        logging.error(br)
        # try send without reply
        asyncio.get_event_loop().run_until_complete(
            bot.send_message(
                chat_id=payload["chat_id"],
                text=f"*{payload['engine']}*: {message}",
                disable_notification=True,
                disable_web_page_preview=True,
            )
        )
    except Exception as e:
        logging.error(f"Cannot send message, error: {e}, \nPayload: {payload}")
        # try send plaintext
        asyncio.get_event_loop().run_until_complete(
            bot.send_message(
                chat_id=payload["chat_id"],
                text=f"*{payload['engine']}*: {message}",
                reply_to_message_id=message_id,
                disable_notification=True,
                disable_web_page_preview=True,
            )
        )


def __send_images(payload: list) -> None:
    # message_id = payload["message_id"]
    message = utils.decode_message(payload["response"])
    logging.info(message)
    for url in iter(message.splitlines()):
        asyncio.get_event_loop().run_until_complete(
            bot.send_photo(
                chat_id=payload["chat_id"],
                photo=url,
                caption=payload["text"],
                # reply_to_message_id=message_id,
                disable_notification=True,
            )
        )
