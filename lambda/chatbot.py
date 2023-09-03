import asyncio
import json
import logging
import time

import boto3
import boto3.session
from botocore.exceptions import ClientError
from telegram import (
    BotCommand,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    constants,
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

from .help_command import help_handler, start_handler
from .user_config import UserConfig
from .utils import (
    generate_transcription,
    read_ssm_param,
    recursive_stringify,
    send_typing_action,
    split_long_message,
)

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


async def set_commands(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("/start", "Begin with bot, introduction"),
            BotCommand("/help", "Commands usage. Syntax: /help COMMAND"),
            BotCommand("/tr", "Translate text to other language(s)"),
            BotCommand("/engines", "Gets or sets the AI model(s)"),
            BotCommand("/bing", "Switch to Bing AI model"),
            BotCommand("/bard", "Switch to Google Bard AI model"),
            BotCommand("/chatgpt", "Switch to OpenAI ChatGPT model"),
            BotCommand("/llama", "Switch to LLama 2 AI model"),
            BotCommand("/creative", "Set tone of responses to more creative (Default)"),
            BotCommand("/balanced", "Set tone of responses to more balanced"),
            BotCommand("/precise", "Set tone of responses to more precise"),
            BotCommand("/imagine", "Generate images with DALL-E engine"),
        ]
    )


telegram_token = read_ssm_param(param_name="TELEGRAM_TOKEN")
sns_topic = read_ssm_param(param_name="REQUESTS_SNS_TOPIC_ARN")
admins = [read_ssm_param(param_name="TELEGRAM_BOT_ADMINS")]
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
logging.info(f"admins:{admins}")

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
    style = update.message.text.strip("/").split("@")[0].lower()
    config["style"] = style
    logging.info(f"user: {user_id} set engine style to: '{style}'")
    user_config.write(user_id, config)
    await update.message.reply_text(text=f"Bot engine style has been set to '{style}'")


async def set_engines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    engine_types = (
        update.message.text.strip("/")
        .split("@")[0]
        .lower()
        .replace("engines", "")
        .replace(" ", "")
        .strip()
    )
    logging.info(f"engines: {engine_types}")
    if not engine_types:
        engine_types = config["engines"]
        await update.message.reply_text(text=f"Bot engine(s): {engine_types}")
        return
    if "," in engine_types:
        config["engines"] = engine_types.split(",")
    else:
        config["engines"] = [engine_types]
    logging.info(f"user: {user_id} set engine to: {engine_types}")
    user_config.write(user_id, config)
    await update.message.reply_text(text=f"Bot engine has been set to {engine_types}")


@send_typing_action
async def send_example(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update.message.text = example_tg
    await process_message(update, context)


@send_typing_action
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await process_message(update, context)


@send_typing_action
async def imagine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = int(update.message.from_user.id)
        config = user_config.read(user_id)
        await __process_images(update, context, config, "images")
    except Exception as e:
        logging.error(str(e))


@send_typing_action
async def ideogram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = int(update.message.from_user.id)
        config = user_config.read(user_id)
        # logging.info(f"ideogram: user_id {user_id}, config: {config}")
        await __process_images(update, context, config, "ideogram")
    except Exception as e:
        logging.error(str(e))


async def grab_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id not in admins:
        return
    try:
        query_string = "fields @message | filter @message like /Error/"
        results = __query_cloudwatch_logs(query_string)
        length = len(results)
        logging.info(f"No. of errors: {length}")
        logging.info(f"{results}")
        if length == 0:
            results = ["No error messages found"]
        else:
            text = recursive_stringify(results)
            parts = split_long_message(text, "logs", 4060)
            for part in parts:
                await update.message.reply_text(text=part)

    except Exception as e:
        logging.error(e)
        await update.message.reply_text(
            text=f"Error: ```{str(e)}```",
            parse_mode=constants.ParseMode.MARKDOWN_V2,
        )


def __query_cloudwatch_logs(query_string):
    client = boto3.client("logs")
    try:
        group_response = client.describe_log_groups(logGroupNamePattern="Handler")
        group_names = [group["logGroupName"] for group in group_response["logGroups"]]
        logging.info(group_names)
        response = client.start_query(
            logGroupNames=group_names,
            startTime=int((time.time() - 3600 * 3) * 1000),  # 3h in ms
            endTime=int(time.time() * 1000),
            queryString=query_string,
            limit=1000,
        )
        query_id = response["queryId"]
        while True:
            query_status = client.get_query_results(queryId=query_id)
            if query_status["status"] == "Complete":
                break
            time.sleep(1)

        query_results = query_status["results"]
        return query_results

    except Exception as e:
        logging.error(f"Error querying CloudWatch Logs:{e}")
        return []


async def redrive_dlq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id not in admins:
        return
    await update.message.reply_text(text="Starting Redrive DLQ task")
    results = __start_redrive_dlq()
    await update.message.reply_text(
        text=f"Result: ```{json.dumps(results)}```",
        parse_mode=constants.ParseMode.MARKDOWN_V2,
    )


def __start_redrive_dlq() -> dict:
    session = boto3.session.Session()
    region = session.region_name
    sts_client = session.client("sts")
    account_id = sts_client.get_caller_identity()["Account"]
    dlq_arn = f"arn:aws:sqs:{region}:{account_id}:Request-Queues-DLQ"
    sqs = session.client("sqs")
    try:
        dlq_response = sqs.start_message_move_task(SourceArn=dlq_arn)
        if dlq_response is not None:
            handle = dlq_response["TaskHandle"]
            logging.info(f"Redrive task started: {handle}")
            while True:
                list_response = sqs.list_message_move_tasks(SourceArn=dlq_arn)
                results = list_response["Results"][0]
                logging.info(f"Results: {results}")
                if results["Status"] == "RUNNING":
                    logging.info("Delaying..")
                    time.sleep(2)
                else:
                    break

            logging.info(f"Finished: {results}")
            return results
    except ClientError as e:
        logging.error(f"Redriving DLQ messages error :{e}")
        return f"DLQ Redrive failed: {e}"


# Translation handlers


async def tr_start(update: Update, context: CallbackContext) -> int:
    """Starts the conversation and asks the user about target language"""

    user_id = update.effective_user.id
    config = user_config.read(user_id)

    # Check if the user provided languages to the command
    if len(context.args) > 0:
        logging.info(update.message.text)
        logging.info(context.args)
        langs = ",".join(context.args).strip().upper()
        user_config.write(user_id, config)
        await update.message.reply_text(
            f"Set language(s) to: {langs}. Send your text to translate"
        )
        return TEXT

    reply_keyboard = [
        ["BG", "ZH", "CS", "DA", "NL"],
        ["EL", "EN-GB", "EN-US", "ES", "ET"],
        ["FI", "FR", "DE", "HU", "ID"],
        ["IT", "JP", "KO", "LV", "LT"],
        ["NO", "PL", "PT", "RO", "RU"],
        ["SK", "SL", "SV", "TR", "UA"],
    ]
    await update.message.reply_text(
        "Choose translation language(s)",
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard,
            one_time_keyboard=True,
            selective=True,
            input_field_placeholder=getattr(config, "languages", "pl,en-gb").upper(),
        ),
    )
    return LANG


async def tr_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the selected language and asks for a text"""
    user_id = update.effective_user.id
    config = user_config.read(user_id)
    if update.message.text is not None:
        config["languages"] = update.message.text.strip().upper()
    user_config.write(user_id, config)
    await update.message.reply_text(
        "Please send your text to translate",
        reply_markup=ReplyKeyboardRemove(),
    )
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
    logging.info("user %s canceled the translation.", user.first_name)
    await update.message.reply_text("OK, bye!", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# Telegram handlers


async def process_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice_message = update.message.voice
    file_id = voice_message.file_id
    file = await bot.get_file(file_id)
    transcript_msg = generate_transcription(file)
    logging.info(transcript_msg)
    try:
        user_id = int(update.effective_message.from_user.id)
        config = user_config.read(user_id)
        await __process_text(update, context, config)
    except Exception as e:
        logging.error(e)


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.text is None:
        return
    if bot.name not in update.message.text and "group" in update.message.chat.type:
        return
    try:
        user_id = int(update.message.from_user.id)
        config = user_config.read(user_id)
        await __process_text(update, context, config)
    except Exception as e:
        logging.error(e)


@send_typing_action
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
    # logging.info(envelop)
    engines = json.dumps(config["engines"])
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


@send_typing_action
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
    # logging.info(envelop)
    engines = json.dumps("deepl")
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


# @send_typing_action
async def __process_images(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    config: UserConfig,
    type: str,
):
    prompt = " ".join(context.args)
    logging.info(prompt)
    envelop = {
        "type": type,
        "user_id": update.effective_user.id,
        "update_id": update.update_id,
        "message_id": update.effective_message.id,
        "text": prompt,
        "chat_id": update.effective_chat.id,
        "timestamp": update.effective_message.date.timestamp(),
        "config": config,
    }
    logging.info(envelop)
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
    app.add_handler(CommandHandler("start", start_handler, filters=filters.COMMAND))
    app.add_handler(CommandHandler("reset", reset, filters=filters.COMMAND))
    app.add_handler(
        CommandHandler(
            ["bing", "chatgpt", "bard", "llama"],
            set_engines,
            filters=filters.COMMAND,
        )
    )
    app.add_handler(CommandHandler("engines", set_engines, filters=filters.COMMAND))
    app.add_handler(
        CommandHandler(
            ["creative", "balanced", "precise"], set_style, filters=filters.COMMAND
        )
    )
    app.add_handler(CommandHandler("help", help_handler, filters=filters.COMMAND))
    # app.add_handler(CommandHandler("example", send_example, filters=filters.COMMAND))
    app.add_handler(CommandHandler("errors", grab_logs, filters=filters.COMMAND))
    app.add_handler(CommandHandler("redrive", redrive_dlq, filters=filters.COMMAND))
    app.add_handler(CommandHandler("ping", ping, filters=filters.COMMAND))
    app.add_handler(CommandHandler("imagine", imagine, filters=filters.COMMAND))
    app.add_handler(CommandHandler("ideogram", ideogram, filters=filters.COMMAND))
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("tr", tr_start, filters=filters.COMMAND)],
        states={
            LANG: [
                MessageHandler(
                    filters.Regex(r"^([a-zA-Z]{2}(\-[a-zA-Z]{2})*,*\s*)+$"), tr_lang
                )
            ],
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
