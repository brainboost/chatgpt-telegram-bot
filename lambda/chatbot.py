import asyncio
import json
import logging
import utils
import boto3
from EdgeGPT import Chatbot
from bing_gpt import BingGpt
from bing_gpt_mock import BingGptMock
from chatsonic import ChatSonic
from engines import EngineInterface, Engines
from telegram import ReplyKeyboardMarkup, Update, constants
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from user_config import UserConfig

logging.basicConfig()
logging.getLogger().setLevel("INFO")

user_config = UserConfig()
# s3_path = utils.read_param(param_name="COOKIES_FILE")
# bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
# chatbot = Chatbot(cookies=utils.read_json_from_s3(bucket_name, file_name))

bing = BingGpt.create()
chatsonic = ChatSonic()

telegram_token = utils.read_param(param_name="TELEGRAM_TOKEN")
app = Application.builder().token(token=telegram_token).build()
bot = app.bot
logging.info("application startup")

# Telegram commands

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bing.reset_chat()
    await context.bot.send_message(
        chat_id=update.message.chat_id, text="Conversation has been reset"
    )

async def set_engine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.create_config(user_id)
    engine = update.message.text.strip("/")
    config["engine"] = Engines[engine]
    user_config.write(user_id, config)
    await context.bot.send_message(
        chat_id=update.message.chat_id, 
        text=f"Bot engine has been set to {Engines[engine]}"
    )

async def set_plaintext(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    # logging.info(update.message.text)
    config["plaintext"] = "plaintext" in update.message.text
    user_config.write(user_id, config)
    await context.bot.send_message(
        chat_id=update.message.chat_id, 
        text=f"Set 'plaintext' to {config['plaintext']}"
    )
    
example = '''*bold* _italic_ ~strikethrough~
`inline code`
```cs 

var = block code;

```
[inline URL](http://www.example.com/)
[inline mention of a user](tg://user?id=123456789)
'''
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
text with links [[1](https://pypi.org/project/adaptivecards/)]  [[2](https://github.com/huuhoa/adaptivecards)]
'''

reply_keyboard = [
    ["Chose next phrase", "Favourite colour"],
    ["Number of siblings", "Something else..."],
    ["Done"],

]

markup = ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True)

async def send_example(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_markdown_v2(example_tg)
    # await update.message.reply_text(collapsible, 2, constants.ParseMode.MARKDOWN_V2))
    # await context.bot.send_message(
    #     chat_id=update.message.chat_id, 
    #     text = collapsible,
    #     parse_mode=constants.ParseMode.HTML
    # )
    # await update.message.reply_text(
    #     "Please press the button below to send a reply to bot",
    #     reply_markup=markup
    #     # ReplyKeyboardMarkup.from_button(
    #     #     KeyboardButton(
    #     #         text="Chose next phrase",
    #     #         # web_app=WebAppInfo(url="https://python-telegram-bot.org/static/webappbot"),
    #     #     )
    #     )
  
async def send_mock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # mock = BingGptMock()
    bing.as
    config = user_config.create_config(0)
    await process_internal(mock, config, update, context)

# Telegram handlers

# @send_typing_action
async def process_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice_message = update.message.voice
    file_id = voice_message.file_id
    file = await bot.get_file(file_id)
    transcript_msg = utils.generate_transcription(file)
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


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text is None:
        return
    if bot.name not in update.message.text and "group" in update.message.chat.type:
        return
    try:
        user_id = int(update.message.from_user.id)
        config = user_config.read(user_id)
        engine = get_engine(config)
        await process_internal(engine, config, update, context)
    except Exception as e:
        logging.error(e)


# @send_typing_action
async def process_internal(engine: EngineInterface, config: UserConfig,
        update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_text = update.message.text.replace(bot.name, "")
    response_msg = await engine.ask(chat_text, config)
        # await context.bot.send_message(
        #     chat_id=chat_id,
        #     allow_sending_without_reply=True,
        #     text=response_msg,
        #     parse_mode=constants.ParseMode.MARKDOWN_V2,
        # )
    await update.message.reply_markdown_v2(response_msg)

def get_engine(config: UserConfig) -> EngineInterface:
    engine_type = Engines(config["engine"])
    switcher = {
        Engines.BING: bing,
        # Engines.CHATGPT: chatgpt,
        Engines.CHATSONIC: chatsonic,
        # Engines.BARD: bard,
    }
    return switcher.get(engine_type, "Not implemented yet")

# Lambda message handler

def message_handler(event, context):
    return asyncio.get_event_loop().run_until_complete(main(event))


async def main(event):
    # app.add_handler(MessageHandler(filters.CHAT, process_message))
    app.add_handler(CommandHandler("reset", reset, filters=filters.COMMAND))
    app.add_handler(CommandHandler(["bing","chatgpt","chatsonic","bard"], set_engine, 
        filters=filters.COMMAND))
    app.add_handler(CommandHandler(["plaintext","markdown"], set_plaintext, 
        filters=filters.COMMAND))
    app.add_handler(CommandHandler("example", send_example, filters=filters.COMMAND))
    app.add_handler(CommandHandler("mock", send_mock, filters=filters.COMMAND))
    app.add_handler(MessageHandler(filters.TEXT, process_message))
    app.add_handler(MessageHandler(filters.VOICE, process_voice_message))
    try:
        await app.initialize()
        await app.process_update(Update.de_json(json.loads(event["body"]), bot))

        return {"statusCode": 200, "body": "Success"}

    except Exception as ex:
        logging.error(ex)
        return {"statusCode": 500, "body": "Failure"}
