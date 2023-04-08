import asyncio
import json
import logging

import utils
from bard_engine import BardEngine
from bing_gpt import BingGpt
from engine_interface import EngineInterface
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from user_config import UserConfig

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

user_config = UserConfig()
engines = {}

telegram_token = utils.read_ssm_param(param_name="TELEGRAM_TOKEN")
app = Application.builder().token(token=telegram_token).build()
bot = app.bot
logging.info("application startup")

# Telegram commands

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    engine = get_engine(config)
    engine.reset_chat()
    await update.message.reply_text(
        text="Conversation has been reset"
    )

async def set_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    style = update.message.text.strip("/").lower()
    config["style"] = style
    logging.info(f"user: {user_id} set engine style to: '{style}'")
    user_config.write(user_id, config)
    await update.message.reply_text(
        text=f"Bot engine style has been set to '{style}'"
    )

async def set_engine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    engine_type = update.message.text.strip("/").lower()
    logging.info(f"engine: {engine_type}")
    config["engine"] = engine_type
    logging.info(f"user: {user_id} set engine to: {engine_type}")
    user_config.write(user_id, config)
    await update.message.reply_text(
        text=f"Bot engine has been set to {engine_type}"
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

async def process_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice_message = update.message.voice
    file_id = voice_message.file_id
    file = await bot.get_file(file_id)
    transcript_msg = utils.generate_transcription(file)
    logging.info(transcript_msg)
    try:
        user_id = int(update.effective_message.from_user.id)
        config = user_config.read(user_id)
        engine = get_engine(config)
        await process_internal(engine, config, update, context)
    except Exception as e:
        logging.error(e)


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message is None:
        logging.info(update)
        return
    if bot.name not in update.effective_message.text: 
        # and update.effective_message.chat.type == constants.ChatType.GROUP:
        return
    try:
        user_id = int(update.effective_user.id)
        config = user_config.read(user_id)
        engine = get_engine(config)
        await process_internal(update, context, engine, config)
    except Exception as e:
        logging.error(e)


@utils.send_typing_action
async def process_internal(update: Update, context: ContextTypes.DEFAULT_TYPE,
    engine: EngineInterface, config: UserConfig):
    chat_text = update.effective_message.text.replace(bot.name, "")
    response = await engine.ask_async(chat_text, config)

    if "plaintext" in config is True:
        await update.effective_message.reply_text(
            text=response,
            disable_notification=True,
            disable_web_page_preview=True)
    else:
        await update.effective_message.reply_markdown_v2(
            text=response,
            disable_notification=True,
            disable_web_page_preview=True)


def get_engine(config: UserConfig) -> EngineInterface:
    engine_type = config["engine"]
    if engine_type in engines:
        return engines[engine_type]
    engine: EngineInterface = None
    if engine_type == "bing":
        engine = BingGpt.create()
    elif engine_type == "bard":
        engine = BardEngine.create()
    engines[engine_type] = engine
    return engine


# Lambda message handler

def message_handler(event, context):
    return asyncio.get_event_loop().run_until_complete(main(event))


async def main(event):
    app.add_handler(CommandHandler("reset", reset, filters=filters.COMMAND))
    app.add_handler(CommandHandler(["bing", "chatgpt", "chatsonic", "bard"], set_engine,
        filters=filters.COMMAND))
    app.add_handler(CommandHandler(["plaintext", "markdown"], set_plaintext,
        filters=filters.COMMAND))
    app.add_handler(CommandHandler(["creative", "balanced", "precise"], set_style, 
        filters=filters.COMMAND))
    app.add_handler(CommandHandler("example", send_example, filters=filters.COMMAND))
    app.add_handler(MessageHandler(filters.ALL, process_message))
    app.add_handler(MessageHandler(filters.VOICE, process_voice_message))
    try:
        await app.initialize()
        await app.process_update(Update.de_json(json.loads(event["body"]), bot))
        return {"statusCode": 200, "body": "Success"}

    except Exception as ex:
        logging.error(ex)
        return {"statusCode": 500, "body": "Failure"}
