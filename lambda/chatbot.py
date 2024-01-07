import asyncio
import json
import logging
import time
from typing import Any, Optional

import boto3
import boto3.session
from botocore.exceptions import ClientError
from telegram import (
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
    escape_markdown_v2,
    generate_transcription,
    read_ssm_param,
    recursive_stringify,
    restricted,
    send_action,
    send_typing_action,
    split_long_message,
    upload_to_s3,
)

LANG, TEXT = range(2)

logging.basicConfig()
logging.getLogger().setLevel("INFO")

user_config = UserConfig()
sns = boto3.session.Session().client("sns")


telegram_token = read_ssm_param(param_name="TELEGRAM_TOKEN")
sns_topic = read_ssm_param(param_name="REQUESTS_SNS_TOPIC_ARN")
admins = [read_ssm_param(param_name="TELEGRAM_BOT_ADMINS")]
app = (
    Application.builder()
    .token(token=telegram_token)
    .concurrent_updates(True)
    .http_version("1.1")
    .get_updates_http_version("1.1")
    .build()
)
bot = app.bot
logging.info("application startup")
logging.info(f"admins:{admins}")

# Telegram commands


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        update.effective_user is None
        or update.effective_message is None
        or update.effective_message.text is None
    ):
        return

    user_id = update.effective_user.id
    config = user_config.read(user_id)
    envelop = {
        "type": "command",
        "user_id": update.effective_user.id,
        "username": update.effective_user.name,
        "update_id": update.update_id,
        "message_id": update.effective_message.id,
        "text": update.effective_message.text,
        "chat_id": getattr(update.effective_chat, "id", None),
        "timestamp": update.effective_message.date.timestamp,
        "engines": config["engines"],
    }
    sns.publish(TopicArn=sns_topic, Message=json.dumps(envelop))
    await update.effective_message.reply_text(text="Conversation has been reset")


async def set_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        update.effective_user is None
        or update.effective_message is None
        or update.effective_message.text is None
    ):
        return

    user_id = update.effective_user.id
    config = user_config.read(user_id)
    style = update.effective_message.text.strip("/").split("@")[0].lower()
    config["style"] = style
    logging.info(f"user: {user_id} set engine style to: '{style}'")
    user_config.write(user_id, config)
    await update.effective_message.reply_text(
        text=f"Bot engine style has been set to '{style}'"
    )


@send_typing_action
async def engines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        update.effective_user is None
        or update.effective_message is None
        or update.effective_message.text is None
    ):
        return

    user_id = update.effective_user.id
    config = user_config.read(user_id)
    username = update.effective_user.username
    config["username"] = username
    engine_types = (
        update.effective_message.text.strip("/")
        .split("@")[0]
        .lower()
        .replace("engines", "")
        .replace(" ", "")
        .strip()
    )
    logging.info(f"engines: {engine_types}")
    if not engine_types:
        engine_types = config["engines"]
        await update.effective_message.reply_text(text=f"Bot engines: {engine_types}")
        return

    if "," in engine_types:
        config["engines"] = engine_types.split(",")
    else:
        config["engines"] = [engine_types]
    logging.info(f"User {username} {user_id} set engines to '{engine_types}'")
    user_config.write(user_id, config)
    await update.effective_message.reply_text(
        text=f"Bot engines has been set to {engine_types}"
    )


@restricted(admins)
@send_typing_action
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await process_message(update, context)


@send_action(constants.ChatAction.UPLOAD_PHOTO)
async def imagine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        update.effective_user is None
        or update.effective_message is None
        or update.effective_message.text is None
    ):
        return

    user_id = update.effective_user.id
    config = user_config.read(user_id)
    command = update.effective_message.text.strip("/").split()[0].lower()
    try:
        await __process_images(update, context, config, command)
    except Exception as e:
        logging.error(str(e))
        await update.effective_message.reply_text(
            text="An error occured when trying to generate images"
        )


@send_typing_action
@restricted(admins)
async def grab_errors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
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
                await update.effective_message.reply_text(text=part)
    except Exception as e:
        logging.error(e)
        await update.effective_message.reply_text(
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
            startTime=int((time.time() - 3600 * 3) * 1000),  # 3h
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


@send_typing_action
@restricted(admins)
async def redrive_dlq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(text="Starting Redrive DLQ")
    results = __start_redrive_dlq()
    await update.effective_message.reply_text(
        text=results,
        parse_mode=constants.ParseMode.MARKDOWN_V2,
    )


def __start_redrive_dlq() -> Any:
    session = boto3.session.Session()
    sqs = session.client("sqs")
    sns = session.client("sns")
    count = 0
    for queue_url in sqs.list_queues()["QueueUrls"]:
        if "-DLQ" in queue_url:
            logging.info(queue_url)
            try:
                while True:
                    messages = sqs.receive_message(
                        QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=10
                    )
                    if "Messages" in messages:
                        for msg in messages["Messages"]:
                            receipt_handle = msg["ReceiptHandle"]
                            body = json.loads(msg["Body"])
                            record = body["Records"][0]
                            if "sns" in record["EventSource"]:
                                topic = record["Sns"]["TopicArn"]
                                payload = record["Sns"]["Message"]
                                logging.info(payload)
                                attributes = record["Sns"]["MessageAttributes"]
                                attrs = {
                                    "type": {
                                        "DataType": "String",
                                        "StringValue": attributes["type"]["Value"],
                                    },
                                    "engines": {
                                        "DataType": "String.Array",
                                        "StringValue": attributes["engines"]["Value"],
                                    },
                                }
                                logging.info(attrs)
                                resp = sns.publish(
                                    TopicArn=topic,
                                    MessageStructure="json",
                                    MessageAttributes=attrs,
                                    Message=json.dumps({"default": payload}),
                                )
                                logging.info(
                                    f"Published to SNS topic {topic}. MessageId: {resp['MessageId']}"
                                )
                                count += 1
                                sqs.delete_message(
                                    QueueUrl=queue_url, ReceiptHandle=receipt_handle
                                )
                                logging.info(f"Message deleted from {queue_url}")
                            else:
                                logging.error(
                                    f"Redrive is only available for SNS. Actual event source: {record['EventSource']}"
                                )
                                logging.info(record)
                    else:
                        logging.info(f"Queue is empty: {queue_url}")
                        break
            except ClientError as e:
                logging.error(f"Redriving DLQ messages error :{e}")
                return f"DLQ Redrive failed for {queue_url}"
    return escape_markdown_v2("Finished DLQ redrive. {} messages moved".format(count))


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


@send_typing_action
async def process_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("voice message in 'process_voice_message'")
    voice_message = update.message.voice
    file_id = voice_message.file_id
    logging.info(file_id)
    file = await bot.get_file(file_id)
    transcript_msg = await generate_transcription(file)
    logging.info(transcript_msg)
    await update.effective_message.reply_text(
        text=escape_markdown_v2(transcript_msg), disable_notification=True
    )
    try:
        user_id = int(update.effective_message.from_user.id)
        config = user_config.read(user_id)
        envelop = {
            "type": "text",
            "user_id": user_id,
            "username": update.effective_user.name,
            "update_id": update.update_id,
            "message_id": update.effective_message.id,
            "text": transcript_msg,
            "chat_id": update.effective_chat.id,
            "timestamp": update.effective_message.date.timestamp(),
            "config": config,
        }
        await __send_envelop(envelop, json.dumps(config["engines"]))
    except Exception as e:
        logging.error(
            msg="Exception occured during voice message processing",
            exc_info=e,
        )


@send_typing_action
async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.photo is None:
        return
    logging.info("File upload in 'process_photo'")
    # logging.info(update.message)
    caption = update.message.caption
    if bot.name not in caption and "group" in update.message.chat.type:
        return
    s3_bucket = read_ssm_param(param_name="BOT_S3_BUCKET")
    photo = max(update.message.photo, key=lambda x: x.file_size)
    logging.info(photo)
    file_id = photo.file_id
    logging.info(file_id)
    try:
        file = await bot.get_file(file_id)
        path = await upload_to_s3(file, s3_bucket, "att", f"{photo.file_unique_id}.jpg")
        logging.info(f"File uploaded {path}")
        user_id = int(update.effective_user.id)
        config = user_config.read(user_id)
        envelop = {
            "type": "text",
            "user_id": user_id,
            "username": update.effective_user.name,
            "update_id": update.update_id,
            "message_id": update.effective_message.id,
            "text": caption,
            "chat_id": update.effective_chat.id,
            "timestamp": update.effective_message.date.timestamp(),
            "config": config,
            "file": path,
        }
        # logging.info(envelop)
        await __send_envelop(envelop, json.dumps(config["engines"]))
    except Exception as e:
        logging.error(
            msg="Exception occured during processing of the picture",
            exc_info=e,
        )


async def process_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("File upload in 'process_attachment'")
    if update.message is None:
        return
    logging.info(update.message)
    caption = update.message.caption
    if bot.name not in caption and "group" in update.message.chat.type:
        return
    attachment = update.message.effective_attachment
    logging.info(attachment)
    file_id = attachment.file_id
    logging.info(file_id)
    s3_bucket = read_ssm_param(param_name="BOT_S3_BUCKET")
    try:
        file = await bot.get_file(file_id)
        path = await upload_to_s3(file, s3_bucket, "att", attachment.file_name)
        logging.info(f"File uploaded {path}")
        user_id = int(update.effective_user.id)
        config = user_config.read(user_id)
        envelop = {
            "type": "text",
            "user_id": user_id,
            "username": update.effective_user.name,
            "update_id": update.update_id,
            "message_id": update.effective_message.id,
            "text": caption,
            "chat_id": update.effective_chat.id,
            "timestamp": update.effective_message.date.timestamp(),
            "config": config,
            "file": path,
        }
        # logging.info(envelop)
        await __send_envelop(envelop, json.dumps(config["engines"]))
    except Exception:
        logging.error(
            msg="Exception occured during processing of the attachment",
            exc_info=context.error,
        )


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.text is None:
        return
    if bot.name not in update.message.text and "group" in update.message.chat.type:
        return
    try:
        user_id = int(update.message.from_user.id)
        config = user_config.read(user_id)
        await __process_text(update, context, config)
    except Exception:
        logging.error(
            msg="Exception occured during processing of the message",
            exc_info=context.error,
        )


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
        "username": update.effective_user.name,
        "update_id": update.update_id,
        "message_id": update.effective_message.id,
        "text": chat_text,
        "chat_id": update.effective_chat.id,
        "timestamp": update.effective_message.date.timestamp(),
        "config": config,
    }
    await __send_envelop(envelop, json.dumps(config["engines"]))


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
        "username": update.effective_user.name,
        "update_id": update.update_id,
        "message_id": update.effective_message.id,
        "text": text,
        "chat_id": update.effective_chat.id,
        "timestamp": update.effective_message.date.timestamp(),
        "languages": lang.upper(),
    }
    await __send_envelop(envelop, "deepl")


async def __process_images(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    config: dict,
    img_type: str,
):
    if context.args is None:
        return

    prompt = " ".join(context.args)
    logging.info(prompt)
    envelop = {
        "type": img_type,
        "user_id": update.effective_user.id,
        "username": update.effective_user.name,
        "update_id": update.update_id,
        "message_id": update.effective_message.id,
        "text": prompt,
        "chat_id": update.effective_chat.id,
        "timestamp": update.effective_message.date.timestamp(),
        "config": config,
    }
    logging.info(envelop)
    await __send_envelop(envelop)


async def __send_envelop(envelop: Any, engines: Optional[str] = None) -> None:
    logging.info(
        "Sending envelop to topic {} with engines {}".format(sns_topic, engines)
    )
    attrs = {
        "type": {"DataType": "String", "StringValue": envelop["type"]},
    }
    if engines:
        attrs["engines"] = {"DataType": "String.Array", "StringValue": engines}
    try:
        sns.publish(
            TopicArn=sns_topic,
            Message=json.dumps(envelop),
            MessageAttributes=attrs,
        )
    except Exception as e:
        logging.error("Can't send envelop to request topic", exc_info=e)


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
            ["bing", "chatgpt", "bard", "llama", "claude", "gemini"],
            engines,
            filters=filters.COMMAND,
        )
    )
    app.add_handler(CommandHandler("engines", engines, filters=filters.COMMAND))
    app.add_handler(
        CommandHandler(
            ["creative", "balanced", "precise"], set_style, filters=filters.COMMAND
        )
    )
    app.add_handler(CommandHandler("help", help_handler, filters=filters.COMMAND))
    app.add_handler(CommandHandler("errors", grab_errors, filters=filters.COMMAND))
    app.add_handler(CommandHandler("redrive", redrive_dlq, filters=filters.COMMAND))
    app.add_handler(CommandHandler("ping", ping, filters=filters.COMMAND))
    app.add_handler(CommandHandler("imagine", imagine, filters=filters.COMMAND))
    app.add_handler(CommandHandler("ideogram", imagine, filters=filters.COMMAND))
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
    app.add_handler(
        MessageHandler(filters=filters.VOICE, callback=process_voice_message)
    )
    app.add_handler(MessageHandler(filters=filters.PHOTO, callback=process_photo))
    app.add_handler(
        MessageHandler(filters=filters.ATTACHMENT, callback=process_attachment)
    )
    app.add_handler(MessageHandler(filters=filters.ALL, callback=process_message))

    try:
        await app.initialize()
        update = Update.de_json(json.loads(event["body"]), bot)
        await app.process_update(update)
        return {"statusCode": 200, "body": "Success"}

    except Exception as ex:
        logging.error(ex)
        return {"statusCode": 500, "body": "Failure"}
