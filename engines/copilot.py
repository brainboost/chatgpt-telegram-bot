import asyncio
import json
import logging
import os
from typing import Any

import boto3
from sydney import SydneyClient

from .common_utils import (
    encode_message,
    escape_markdown_v2,
    get_image,
    json_cookies_to_header_string,
    read_json_from_s3,
    read_ssm_param,
)
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "copilot"

bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")


async def process_command(
    input: str,
    context: UserContext,
    chatbot: SydneyClient,
) -> None:
    command = input.removeprefix(prefix="/").lower()
    logging.info(f"Processing command {command} for {context.user_id}")
    if "reset" in command:
        chatbot.close_conversation()
        context.reset_conversation()
        logging.info(f"Conversation hass been reset for {context.user_id}")
        return
    logging.error(f"Unknown command {command}")


async def ask(
    text: str,
    chatbot: SydneyClient,
    context: UserContext,
    file_path: str = None,
) -> str:
    if "/ping" in text:
        return "pong"

    if file_path:
        logging.info(f"Downloading image '{file_path}'")
        image = get_image(file_path, bucket_name)
    else:
        image = None

    response = await chatbot.ask(prompt=text, attachment=image)
    logging.info(response)
    return escape_markdown_v2(response)


async def create(style: str) -> SydneyClient:
    logging.info("Create chatbot instance")
    bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
    cookie_json = read_json_from_s3(bucket_name, "copilot-cookies.json")
    os.environ["BING_COOKIES"] = json_cookies_to_header_string(cookie_json)
    sydney = SydneyClient(
        style=style,
        bing_cookies=json_cookies_to_header_string(cookie_json),
        use_proxy=False,
    )
    await sydney.start_conversation()
    return sydney


async def __process_payload(payload: Any, request_id: str) -> None:
    user_id = payload["user_id"]
    user_context = UserContext(
        user_id=f"{user_id}_{payload['chat_id']}",
        request_id=request_id,
        engine_id=engine_type,
        username=payload["username"],
    )
    style = payload["config"].get("style", "creative")
    instance = await create(style)
    user_context.conversation_id = instance.conversation_id
    if "command" in payload["type"]:
        await process_command(
            input=payload["text"], context=user_context, chatbot=instance
        )
        return
    response = await ask(
        text=payload["text"],
        chatbot=instance,
        context=user_context,
        file_path=payload.get("file", None),
    )
    user_context.save_conversation(
        conversation={"request": payload["text"], "response": response},
    )
    payload["response"] = encode_message(response)
    payload["engine"] = engine_type
    sns.publish(TopicArn=result_topic, Message=json.dumps(payload))
    instance.close_conversation()


def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        asyncio.get_event_loop().run_until_complete(
            __process_payload(payload, request_id)
        )
