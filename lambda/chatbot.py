import asyncio
import json
import logging

import boto3
from bing_gpt import BingGpt
from telegram import Update, constants
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from user_config import UserConfig
from utils import generate_transcription, send_typing_action

example_tg = '''
*bold \*text*
_italic \*text_
__underline__
~strikethrough~
||spoiler||
*bold _italic bold ~italic bold strikethrough ||italic bold strikethrough spoiler||~ __underline italic bold___ bold*
[inline URL](http://www.example.com/)
[inline mention of a user](tg://user?id=123456789)
`inline fixed-width code`
```
pre-formatted fixed-width code block
```
```python
pre-formatted fixed-width code block written in the Python programming language
```
text with links. And dots. \[[1](https://pypi.org/project/adaptivecards/)\]  [\[2\]](https://github.com/huuhoa/adaptivecards)
'''

logging.basicConfig()
logging.getLogger().setLevel("INFO")
bing = BingGpt()
user_config = UserConfig()

telegram_token = boto3.client(service_name="ssm").get_parameter(Name="TELEGRAM_TOKEN")[
    "Parameter"
]["Value"]
app = Application.builder().token(token=telegram_token).build()
bot = app.bot
logging.info("application startup")

# Telegram commands

async def reset(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bing.reset_chat()
    await context.bot.send_message(
        chat_id=update.message.chat_id, text="Conversation has been reset"
    )

async def set_engine(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    logging.info(f"user_id: {user_id}")
    config = user_config.create_config(user_id)
    logging.info(f"config: {config}")
    engine = update.message.text.strip("/").lower()
    logging.info(f"engine: {engine}")
    config["engine"] = engine
    logging.info(f"config: {config}")
    user_config.write(user_id, config)
    await update.message.reply_text(
        text=f"Bot engine has been set to {engine}"
    )

async def set_plaintext(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    config["plaintext"] = "plaintext" in update.message.text.lower()
    user_config.write(user_id, config)
    await update.message.reply_text(
        text=f"Option 'plaintext' was set to {config['plaintext']}"
    )

async def send_example(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_markdown_v2(
        text=BingGpt.escape_markdown_v2(text=example_tg), 
        disable_web_page_preview=True)


# Telegram handlers

# @send_typing_action
async def process_voice_message(update, context: ContextTypes.DEFAULT_TYPE):
    voice_message = update.message.voice
    file_id = voice_message.file_id
    file = await bot.get_file(file_id)
    transcript_msg = generate_transcription(file)
    logging.info(transcript_msg)
    user_id = int(update.message.from_user.id)
    config = user_config.read(user_id)
    message = await bing.ask(transcript_msg, config)
    chat_id = update.message.chat_id
    await context.bot.send_message(
        chat_id=chat_id,
        text=message,
        parse_mode=constants.ParseMode.MARKDOWN_V2,
    )


async def process_message(update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text is None:
        return
    if bot.name not in update.message.text and "group" in update.message.chat.type:
        return
    try:
        await processing_internal(update, context)
    except Exception as e:
        logging.error(e)


@send_typing_action
async def processing_internal(update, context: ContextTypes.DEFAULT_TYPE):
    # chat_id = update.message.chat_id
    chat_text = update.message.text.replace(bot.name, "")
    try:
        user_id = int(update.message.from_user.id)
        config = user_config.read(user_id)
        response = await bing.ask(chat_text, config)
        if "plaintext" in config is True:
            await update.message.reply_text(
                text=BingGpt.read_plain_text(response=response),
                disable_notification=True)
        else:
            await update.message.reply_markdown_v2(
                text=BingGpt.read_markdown(response=response),
                disable_notification=True,
                disable_web_page_preview=True)
    except Exception as e:
        logging.error(e)


# Lambda message handler

def message_handler(event, context):
    return asyncio.get_event_loop().run_until_complete(main(event))


async def main(event):
    app.add_handler(CommandHandler("reset", reset, filters=filters.COMMAND))
    app.add_handler(CommandHandler(["bing", "chatgpt", "chatsonic", "bard"], set_engine,
        filters=filters.COMMAND))
    app.add_handler(CommandHandler(["plaintext", "markdown"], set_plaintext,
        filters=filters.COMMAND))
    app.add_handler(CommandHandler("example", send_example, filters=filters.COMMAND))
    app.add_handler(MessageHandler(filters.TEXT, process_message))
    # app.add_handler(MessageHandler(filters.CHAT, process_message))
    app.add_handler(MessageHandler(filters.VOICE, process_voice_message))
    try:
        await app.initialize()
        await app.process_update(Update.de_json(json.loads(event["body"]), bot))

        return {"statusCode": 200, "body": "Success"}

    except Exception as ex:
        logging.error(ex)
        return {"statusCode": 500, "body": "Failure"}
