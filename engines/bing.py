import asyncio
import json
import logging
import re

import boto3
from EdgeGPT.EdgeGPT import CONVERSATION_STYLE_TYPE, Chatbot

from .common_utils import (
    encode_message,
    escape_markdown_v2,
    read_json_from_s3,
    read_ssm_param,
)
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "bing"
remove_links_pattern = re.compile(r"\[\^\d+\^\]\s?")
ref_link_pattern = re.compile(r"\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?")


def process_command(input: str, context: UserContext) -> None:
    command = input.removeprefix(prefix="/").lower()
    logging.info(f"Processing command {command} for {context.user_id}")
    if "reset" in command:
        context.reset_conversation()
        logging.info(f"Conversation hass been reset for {context.user_id}")
        return
    logging.error(f"Unknown command {command}")


def ask(
    text: str,
    chatbot: Chatbot,
    style: CONVERSATION_STYLE_TYPE,
    context: UserContext,
) -> str:
    if "/ping" in text:
        return "pong"

    response = asyncio.run(
        chatbot.ask(
            prompt=text,
            conversation_style=style,
            webpage_context=context.conversation_id,
            simplify_response=False,
        ),
    )
    logging.info(response)
    item = response["item"]
    context.conversation_id = item["conversationId"]
    try:
        return __as_markdown(item)
    except Exception as e:
        logging.error(
            f"chatbot.ask finished with an error. Request:{text}, response item:{item}",
            exc_info=e,
        )
        return __as_plaintext(item)


def __as_plaintext(item: dict) -> str:
    return re.sub(
        pattern=remove_links_pattern,
        repl="",
        string=item["messages"][1]["text"],
    )


def __as_markdown(item: dict) -> str:
    message = item["messages"][-1]
    text = message["adaptiveCards"][0]["body"][0]["text"]
    return __replace_references(text)


def __replace_references(text: str) -> str:
    ref_links = re.findall(pattern=ref_link_pattern, string=text)
    text = re.sub(pattern=ref_link_pattern, repl="", string=text)
    text = escape_markdown_v2(text)
    for link in ref_links:
        link_label = link[0]
        link_ref = link[1]
        inline_link = f" [\[{link_label}\]]({link_ref})"
        text = re.sub(
            pattern=rf"\[\^{link_label}\^\]\[\d+\]", repl=inline_link, string=text
        )
    return text


def create() -> Chatbot:
    logging.info("Create chatbot instance")
    bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
    # socks_url = read_ssm_param(param_name="SOCKS5_URL")
    # os.environ["all_proxy"] = socks_url
    chatbot = asyncio.run(
        Chatbot.create(cookies=read_json_from_s3(bucket_name, "bing-cookies.json"))
    )
    return chatbot


results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")


def sqs_handler(event, context):
    """AWS SQS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["body"])
        user_id = payload["user_id"]
        user_context = UserContext(
            user_id=f"{user_id}_{payload['chat_id']}",
            request_id=request_id,
            engine_id=engine_type,
            username=payload["username"],
        )
        if "command" in payload["type"]:
            process_command(input=payload["text"], context=user_context)
            return

        instance = create()
        style = payload["config"].get("style", "creative")
        response = ask(
            text=payload["text"],
            chatbot=instance,
            style=style,
            context=user_context,
        )
        user_context.save_conversation(
            conversation={"request": payload["text"], "response": response},
        )
        payload["response"] = encode_message(response)
        payload["engine"] = engine_type
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
