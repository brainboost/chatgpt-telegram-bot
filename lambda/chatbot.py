import asyncio
import json
import logging

import boto3
import utils
from telegram import (
    BotCommand,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from user_config import UserConfig

example_tg = """
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
text with links. And dots. [\[2\]](https://github.com/huuhoa/adaptivecards)
"""
LANG, TEXT = range(2)

logging.basicConfig()
logging.getLogger().setLevel("INFO")

user_config = UserConfig()
sns = boto3.client("sns")


async def set_commands(application: Application):
    await application.bot.set_my_commands(
        [
            BotCommand("/bing", "Switch to Bing AI model"),
            BotCommand("/bard", "Switch to Google Bard AI model"),
            BotCommand("/chatgpt", "Switch to OpenAI ChatGPT model"),
            BotCommand("/creative", "Set tone of responses to more creative (Default)"),
            BotCommand("/balanced", "Set tone of responses to more balanced"),
            BotCommand("/precise", "Set tone of responses to more precise"),
            BotCommand("/imagine", "Generate images with DALL-E engine"),
            BotCommand("/tr", "Translate text to other language(s)"),
        ]
    )


telegram_token = utils.read_ssm_param(param_name="TELEGRAM_TOKEN")
sns_topic = utils.read_ssm_param(param_name="REQUESTS_SNS_TOPIC_ARN")
app = (
    Application.builder()
    .token(token=telegram_token)
    .concurrent_updates(True)
    .http_version("1.1")
    .get_updates_http_version("1.1")
    .post_init(set_commands)
    .build()
)
bot = app.bot
logging.info("application startup")


# Telegram commands


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    command = update.message.text.strip("/").lower()
    envelop = {
        "type": "command",
        "user_id": update.effective_user.id,
        "update_id": update.update_id,
        "message_id": update.effective_message.id,
        "text": command,
        "chat_id": update.effective_chat.id,
        "timestamp": update.effective_message.date.timestamp,
        "engines": config["engines"],
    }
    sns.publish(TopicArn=sns_topic, Message=json.dumps(envelop))
    await update.message.reply_text(text="Conversation has been reset")


async def set_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    style = update.message.text.strip("/").lower()
    config["style"] = style
    logging.info(f"user: {user_id} set engine style to: '{style}'")
    user_config.write(user_id, config)
    await update.message.reply_text(text=f"Bot engine style has been set to '{style}'")


async def set_engine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    engine_type = update.message.text.strip("/").split()[0].lower()
    logging.info(f"engines: {engine_type}")
    config["engines"] = engine_type
    logging.info(f"user: {user_id} set engine to: {engine_type}")
    user_config.write(user_id, config)
    await update.message.reply_text(text=f"Bot engine has been set to {engine_type}")


@utils.send_typing_action
async def send_example(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update.message.text = example_tg
    await process_message(update, context)


@utils.send_typing_action
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await process_message(update, context)


async def imagine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = int(update.message.from_user.id)
        config = user_config.read(user_id)
        # logging.info(context.args)
        await __process_images(update, context, config)
    except Exception as e:
        logging.error(e)


async def tr_start(update: Update, context: CallbackContext) -> int:
    """Starts the conversation and asks the user about target language"""
    reply_keyboard = [
        ["BG", "CN", "CS", "DA", "NL"],
        ["EN", "ES", "FI", "FR", "DE"],
        ["EL", "HU", "ID", "IT", "JP"],
        ["KO", "LV", "LT", "NO", "PL"],
        ["PG", "RO", "RU", "SK", "SL"],
        ["ES", "SV", "TR", "UA"],
    ]
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    await update.message.reply_text(
        "Choose translation language(s) (separated with comma)",
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard,
            one_time_keyboard=True,
            selective=True,
            input_field_placeholder=config["languages"].upper(),
        ),
    )
    return LANG


async def tr_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the selected language and asks for a text"""
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    if update.message.text.__len__() is not None or update.message.text.__len__() < 2:
        config["languages"] = update.message.text.strip().upper()
    await update.message.reply_text(
        "Please send me your text to translate",
        reply_markup=ReplyKeyboardRemove(),
    )
    user_config.write(user_id, config)
    return TEXT


async def tr_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Run translations"""
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    await __process_translation(
        update,
        context,
        update.message.text,
        config["languages"],
    )
    return ConversationHandler.END


async def tr_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the translation request"""
    user = update.message.from_user
    logging.info("User %s canceled the translation.", user.first_name)
    await update.message.reply_text("OK, bye!", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


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
        await __process_text(update, context, config)
    except Exception as e:
        logging.error(e)


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text is None:
        return
    if bot.name not in update.message.text and "group" in update.message.chat.type:
        return
    try:
        user_id = int(update.message.from_user.id)
        config = user_config.read(user_id)
        await __process_text(update, context, config)
    except Exception as e:
        logging.error(e)


@utils.send_typing_action
async def __process_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    config: UserConfig,
):
    chat_text = update.effective_message.text.replace(bot.name, "")
    envelop = {
        "type": "text",
        "user_id": update.effective_user.id,
        "update_id": update.update_id,
        "message_id": update.effective_message.id,
        "text": chat_text,
        "chat_id": update.effective_chat.id,
        "timestamp": update.effective_message.date.timestamp(),
        "config": config,
    }
    logging.info(envelop)
    engines = json.dumps(config["engines"])
    logging.info(engines)
    try:
        sns.publish(
            TopicArn=sns_topic,
            Message=json.dumps(envelop),
            MessageAttributes={
                "type": {"DataType": "String", "StringValue": envelop["type"]},
                "engines": {
                    "DataType": "String.Array",
                    "StringValue": engines,
                },
            },
        )
    except Exception as e:
        logging.error(e)


@utils.send_typing_action
async def __process_translation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    lang: str = "PL",
):
    envelop = {
        "type": "translate",
        "user_id": update.effective_user.id,
        "update_id": update.update_id,
        "message_id": update.effective_message.id,
        "text": text,
        "chat_id": update.effective_chat.id,
        "timestamp": update.effective_message.date.timestamp(),
        "languages": lang.upper(),
    }
    logging.info(envelop)
    engines = json.dumps("deepl")
    logging.info(engines)
    try:
        sns.publish(
            TopicArn=sns_topic,
            Message=json.dumps(envelop),
            MessageAttributes={
                "type": {"DataType": "String", "StringValue": envelop["type"]},
                "engines": {
                    "DataType": "String.Array",
                    "StringValue": engines,
                },
            },
        )
    except Exception as e:
        logging.error(e)


@utils.send_typing_action
async def __process_images(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    config: UserConfig,
):
    prompt = " ".join(context.args)
    logging.info(prompt)
    envelop = {
        "type": "images",
        "user_id": update.effective_user.id,
        "update_id": update.update_id,
        "message_id": update.effective_message.id,
        "text": prompt,
        "chat_id": update.effective_chat.id,
        "timestamp": update.effective_message.date.timestamp(),
        "config": config,
    }
    logging.info(envelop)
    engines = json.dumps(config["engines"])
    logging.info(engines)
    try:
        sns.publish(
            TopicArn=sns_topic,
            Message=json.dumps(envelop),
            MessageAttributes={
                "type": {"DataType": "String", "StringValue": envelop["type"]},
            },
        )
    except Exception as e:
        logging.error(e)


async def error_handle(update: Update, context: CallbackContext) -> None:
    logging.error(msg="Exception while handling an update:", exc_info=context.error)


# Lambda message handler


def telegram_api_handler(event, context):
    return asyncio.get_event_loop().run_until_complete(_main(event))


async def _main(event):
    app.add_handler(CommandHandler("reset", reset, filters=filters.COMMAND))
    app.add_handler(
        CommandHandler(
            ["bing", "chatgpt", "bard"],
            set_engine,
            filters=filters.COMMAND,
        )
    )
    app.add_handler(
        CommandHandler(
            ["creative", "balanced", "precise"], set_style, filters=filters.COMMAND
        )
    )
    app.add_handler(CommandHandler("example", send_example, filters=filters.COMMAND))
    app.add_handler(CommandHandler("ping", ping, filters=filters.COMMAND))
    app.add_handler(CommandHandler("imagine", imagine, filters=filters.COMMAND))
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("tr", tr_start)],
        states={
            LANG: [MessageHandler(filters.Regex(r"^([a-zA-Z]{2},*\s*)+$"), tr_lang)],
            TEXT: [MessageHandler(filters.TEXT, tr_text)],
        },
        fallbacks=[CommandHandler("cancel", tr_cancel)],
    )
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.ALL, process_message))
    app.add_handler(MessageHandler(filters.VOICE, process_voice_message))

    try:
        await app.initialize()
        update = Update.de_json(json.loads(event["body"]), bot)
        await app.process_update(update)
        return {"statusCode": 200, "body": "Success"}

    except Exception as ex:
        logging.error(ex)
        return {"statusCode": 500, "body": "Failure"}
