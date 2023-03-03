import json
import boto3
import asyncio
import logging
from telegram import constants, Update
from telegram.ext import (
    MessageHandler,
    filters,
    Application,
    ContextTypes,
    CommandHandler,
)
from utils import generate_transcription

# from chatgpt import ChatGPT
# from chatsonic import ChatSonic
from bing import BingGpt

logging.getLogger().setLevel("INFO")
# chat_gpt = ChatGPT()
# chatsonic = ChatSonic()
bing = BingGpt()

telegram_token = boto3.client(service_name="ssm").get_parameter(Name="TELEGRAM_TOKEN")[
    "Parameter"
]["Value"]
application = Application.builder().token(token=telegram_token).build()
bot = application.bot
logging.info("application startup")

# Telegram commands


async def reset(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # chat_gpt.reset_chat()
    # chatsonic.reset_chat()
    bing.reset_chat()
    await context.bot.send_message(
        chat_id=update.message.chat_id, text="Conversation has been reset"
    )


# Telegram handlers


# @send_typing_action
async def process_voice_message(update, context: ContextTypes.DEFAULT_TYPE):
    voice_message = update.message.voice
    file_id = voice_message.file_id
    file = await bot.get_file(file_id)
    transcript_msg = generate_transcription(file)
    logging.info(transcript_msg)
    message = await bing.ask(transcript_msg)
    chat_id = update.message.chat_id
    await context.bot.send_message(
        chat_id=chat_id,
        text=message,
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def process_message(update, context: ContextTypes.DEFAULT_TYPE):
    if bot.name not in update.message.text and "group" in update.message.chat.type:
        # logging.info(f"type: {update.message.chat.type}. Exitting")
        return
    await processing_internal(update, context)


# @send_typing_action
async def processing_internal(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    chat_text = update.message.text.replace(bot.name, "")
    logging.info(f"message: {chat_text}")
    try:
        # response_msg = chat_gpt.ask(chat_text)
        # response_msg = chatsonic.ask(chat_text)
        response_msg = await bing.ask(chat_text)
        logging.info(f"response: {response_msg}")
        await context.bot.send_message(
            chat_id=chat_id,
            allow_sending_without_reply=True,
            text=response_msg,
            parse_mode=constants.ParseMode.MARKDOWN,
        )
    except Exception as e:
        logging.error(e)
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=response_msg,
            parse_mode=constants.ParseMode.MARKDOWN,
        )


# Lambda message handler


def message_handler(event, context):
    return asyncio.get_event_loop().run_until_complete(main(event))


async def main(event):
    application.add_handler(MessageHandler(filters.TEXT, process_message))
    application.add_handler(MessageHandler(filters.CHAT, process_message))
    application.add_handler(MessageHandler(filters.VOICE, process_voice_message))
    application.add_handler(CommandHandler("reset", reset, filters=filters.COMMAND))
    try:
        await application.initialize()
        await application.process_update(Update.de_json(json.loads(event["body"]), bot))

        return {"statusCode": 200, "body": "Success"}

    except Exception as ex:
        logging.error(ex)
        return {"statusCode": 500, "body": "Failure"}
