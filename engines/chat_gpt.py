import json
import logging
from collections import deque
from os import environ
from typing import Any

import boto3
from revChatGPT.V1 import Chatbot

from .common_utils import encode_message, escape_markdown_v2, read_ssm_param
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "chatgpt"


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
    context: UserContext,
) -> str:
    if "/ping" in text:
        return "pong"

    response = deque(
        chatbot.ask(
            prompt=text,
            conversation_id=context.conversation_id,
            parent_id=context.parent_id,
            auto_continue=(context.conversation_id is not None),
        ),
        maxlen=1,
    )[-1]
    message = response["message"]
    context.conversation_id = response["conversation_id"]
    context.parent_id = response["parent_id"]
    logging.info(
        f"Saving context: conversation_id {response['conversation_id']}, parent_id {response['parent_id']}"
    )
    return escape_markdown_v2(message)


def create(context: UserContext) -> Chatbot:
    gpt_token = read_ssm_param(param_name="GPT_TOKEN")
    gpt_proxy = read_ssm_param(param_name="GPT_PROXY")
    http_proxy = read_ssm_param(param_name="SOCKS5_URL")
    environ["CHATGPT_BASE_URL"] = gpt_proxy
    chatbot = Chatbot(
        config={
            "access_token": gpt_token,
            "proxy": http_proxy,
            # "model": "text-davinci-002-render-sha",
        },
        conversation_id=context.conversation_id,
        parent_id=context.parent_id,
        base_url=gpt_proxy,
    )
    return chatbot


result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")


def __process_payload(payload: Any, request_id: str) -> None:
    user_id = payload["user_id"]
    user_context = UserContext(
        user_id=f"{user_id}_{payload['chat_id']}",
        request_id=request_id,
        engine_id=engine_type,
        username=payload["username"],
    )
    instance = create(user_context)
    response = ask(payload["text"], instance, user_context)
    user_context.save_conversation(
        conversation={"request": payload["text"], "response": response},
    )
    payload["response"] = response
    payload["response"] = encode_message(response)
    payload["engine"] = engine_type
    sns.publish(TopicArn=result_topic, Message=json.dumps(payload))


def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        __process_payload(payload, request_id)
