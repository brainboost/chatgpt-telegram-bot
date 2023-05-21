import asyncio
import json
import logging

import utils
from telegram import constants
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
        config = payload["config"]
        message = utils.decode_message(payload["response"])
        if config["plaintext"]:
            parse_mode = None
        else:
            parse_mode = constants.ParseMode.MARKDOWN_V2
        try:
            asyncio.get_event_loop().run_until_complete(
                bot.send_message(
                    chat_id=payload["chat_id"],
                    text=f"*{payload['engine']}*: {message}",
                    parse_mode=parse_mode,
                    reply_to_message_id=message_id,
                    disable_notification=True,
                    disable_web_page_preview=True,
                )
            )
        except Exception as e:
            logging.error(e)
