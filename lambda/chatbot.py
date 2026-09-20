import asyncio
import json
import logging
import time
from typing import Any

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
    CallbackContext,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import providers

from .formatting import assemble_plain_reply, format_text
from .help_command import help_handler, start_handler
from .request_message import (
    CommandRequest,
    IdeogramRequest,
    RequestMessage,
    TextRequest,
    TranslateRequest,
    to_sns_message,
)
from .runtime import create_application, process_update_event
from .user_config import UserConfig
from .utils import (
    generate_transcription,
    read_ssm_param,
    recursive_stringify,
    restricted,
    send_action,
    send_typing_action,
    upload_to_s3,
)

LANG, TEXT = range(2)

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

user_config = UserConfig()
sns = boto3.session.Session().client("sns")


telegram_token = read_ssm_param(param_name="TELEGRAM_TOKEN")
sns_topic = read_ssm_param(param_name="REQUESTS_SNS_TOPIC_ARN")
admins = [read_ssm_param(param_name="TELEGRAM_BOT_ADMINS")]
app = create_application(telegram_token)
bot = app.bot
logging.info("application startup")
logging.info(f"admins:{admins}")


def _request_fields(update: Update) -> dict:
    """Common Telegram identity fields carried by every request kind."""
    return {
        "user_id": update.effective_user.id,
        "chat_id": update.effective_chat.id,
        "username": update.effective_user.name,
        "message_id": update.effective_message.id,
        "update_id": update.update_id,
    }


def _chat_provider(config: dict) -> list[str]:
    """Routing list for one chat request: the conversation-start provider.

    The user-config ``engines`` field (legacy name) holds the chain start —
    which provider begins answering, kept across sessions so follow-ups stay
    on the same model. Provider failures advance the chain worker-side.
    """
    engines = config.get("engines") or []
    if engines:
        return [engines[0]]
    return [providers.DEFAULT_CHAT_PROVIDER]


# Telegram commands


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        update.effective_user is None
        or update.effective_message is None
        or update.effective_message.text is None
    ):
        return

    request = CommandRequest(
        **_request_fields(update),
        text=update.effective_message.text,
    )
    # Memory lives per provider in the failover chain, so clear all of them.
    await __publish(request, engines=list(providers.chat_provider_ids()))
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
async def select_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/gemini | /qwen | /llama — pick the conversation-start provider.

    The choice is persisted in the user config (``engines`` field) so follow-up
    sessions keep answering with the same model; provider failures still fall
    back along the catalog chain automatically.
    """
    if (
        update.effective_user is None
        or update.effective_message is None
        or update.effective_message.text is None
    ):
        return

    user_id = update.effective_user.id
    config = user_config.read(user_id)
    provider = update.effective_message.text.strip("/").split("@")[0].lower()
    if not providers.is_chat_provider(provider):
        valid = ", ".join(providers.chat_provider_ids())
        await update.effective_message.reply_text(
            text=f"Unknown provider '{provider}'. Available: {valid}"
        )
        return

    config["engines"] = [provider]
    user_config.write(user_id, config)
    logger.info(
        "User %s set conversation-start provider to '%s'", user_id, provider
    )
    await update.effective_message.reply_text(
        text=(
            f"Conversations will start with {providers.provider_label(provider)}. "
            "If it is unavailable, the bot falls back automatically."
        )
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
    try:
        await __process_images(update, context, config)
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
        text = recursive_stringify(results)
        parts = assemble_plain_reply(text, "logs")
        for part in parts:
            await update.effective_message.reply_text(text=part)
    except Exception as e:
        logging.error(e)
        await update.effective_message.reply_text(
            text=f"Error: ```{e!s}```",
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
                                    }
                                }
                                if "engines" in attributes:
                                    attrs["engines"] = {
                                        "DataType": "String.Array",
                                        "StringValue": attributes["engines"]["Value"],
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
    return format_text(f"Finished DLQ redrive. {count} messages moved")


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
        text=format_text(transcript_msg),
        disable_notification=True,
        parse_mode=constants.ParseMode.MARKDOWN_V2,
    )
    try:
        user_id = int(update.effective_message.from_user.id)
        config = user_config.read(user_id)
        request = TextRequest(
            **_request_fields(update),
            text=transcript_msg,
            config=config,
        )
        await __publish(request, engines=_chat_provider(config))
    except Exception as e:
        logging.error(
            msg="Exception occured during voice message processing",
            exc_info=e,
        )


@send_typing_action
async def process_upload(
    update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str, file_name: str
) -> None:
    s3_bucket = read_ssm_param(param_name="BOT_S3_BUCKET")
    file = await bot.get_file(file_id)
    path = await upload_to_s3(file, s3_bucket, "att", file_name)
    logging.info(f"File uploaded {path}")
    user_id = int(update.effective_user.id)
    config = user_config.read(user_id)
    request = TextRequest(
        **_request_fields(update),
        text=update.message.caption or "",
        config=config,
    )
    await __publish(request, engines=_chat_provider(config))


async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.photo is None:
        return
    logging.info("File upload in 'process_photo'")
    # logging.info(update.message)
    if bot.name not in update.message.caption and "group" in update.message.chat.type:
        return
    photo = max(update.message.photo, key=lambda x: x.file_size)
    logging.info(photo)
    file_id = photo.file_id
    logging.info(file_id)
    try:
        await process_upload(
            update=update,
            context=context,
            file_id=file_id,
            file_name=f"{photo.file_unique_id}.jpg",
        )
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
    if bot.name not in update.message.caption and "group" in update.message.chat.type:
        return
    attachment = update.message.effective_attachment
    logging.info(attachment)
    file_id = attachment.file_id
    logging.info(file_id)
    try:
        await process_upload(
            update=update,
            context=context,
            file_id=file_id,
            file_name=attachment.file_name,
        )
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
    request = TextRequest(
        **_request_fields(update),
        text=chat_text,
        config=config,
    )
    await __publish(request, engines=_chat_provider(config))


@send_typing_action
async def __process_translation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    lang: str = "PL",
):
    request = TranslateRequest(
        **_request_fields(update),
        text=text,
        languages=lang.upper(),
    )
    await __publish(request)


async def __process_images(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    config: dict,
):
    if context.args is None:
        return

    prompt = " ".join(context.args)
    logging.info(prompt)
    request = IdeogramRequest(
        **_request_fields(update),
        text=prompt,
        config=config,
    )
    logging.info(request.model_dump(exclude_none=True))
    await __publish(request)


async def __publish(request: RequestMessage, engines: list | None = None) -> None:
    """Publish a typed request to the engines topic; transport only."""
    body, attrs = to_sns_message(request, engines)
    logging.info(
        "Publishing %s request to topic %s (engines: %s)",
        request.type,
        sns_topic,
        engines,
    )
    try:
        sns.publish(
            TopicArn=sns_topic,
            Message=body,
            MessageAttributes=attrs,
        )
    except Exception as e:
        logging.error("Can't publish request to request topic", exc_info=e)


async def error_handle(update: Update, context: CallbackContext) -> None:
    """Log handler failures.

    PTB logs handler exceptions itself when no error handler is registered and
    then swallows them, so the Lambda still returns 200; registering this makes
    the failure explicit in CloudWatch with the traceback attached.
    """
    logger.error("Exception while handling an update:", exc_info=context.error)


# Lambda message handler


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to a command no registered handler owns.

    Registered before the generic text handler so stray /commands never leak
    into an engine request.
    """
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        text="Unknown command. Use /help to list available commands."
    )


def register_handlers(app) -> None:
    """Attach the handler tree exactly once, at import time.

    Registration is off the request path: re-registering on every Lambda
    invocation would grow the handler list unboundedly on warm containers.
    Ordering is load-bearing — PTB runs only the first matching handler in a
    group — so known commands come first, then the translation conversation,
    the media handlers, the unknown-command catch-all, and finally the generic
    text handler (which explicitly excludes commands).
    """
    app.add_handler(CommandHandler("start", start_handler, filters=filters.COMMAND))
    app.add_handler(CommandHandler("reset", reset, filters=filters.COMMAND))
    app.add_handler(
        CommandHandler(
            list(providers.chat_provider_ids()),
            select_provider,
            filters=filters.COMMAND,
        )
    )
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
    app.add_handler(MessageHandler(filters=filters.COMMAND, callback=unknown_command))
    app.add_handler(
        MessageHandler(
            filters=filters.TEXT & ~filters.COMMAND, callback=process_message
        )
    )
    # Without this, PTB logs handler failures itself and swallows them.
    app.add_error_handler(error_handle)


register_handlers(app)


def telegram_api_handler(event, context):
    # asyncio.run() creates a fresh loop per invocation; get_event_loop() raises on
    # Python >= 3.12 when no loop is current (as in a Lambda handler thread).
    return asyncio.run(_main(event))


async def _main(event):
    try:
        await process_update_event(event, app)
        return {"statusCode": 200, "body": "Success"}

    except Exception as ex:
        logging.error(ex)
        return {"statusCode": 500, "body": "Failure"}
