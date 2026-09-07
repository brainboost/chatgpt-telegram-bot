import json
import logging
import os
from typing import Any

import boto3
import requests

from .common_utils import encode_message, escape_markdown_v2, read_ssm_param
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

# One shared OpenAI-compatible provider module for all Ollama Cloud engines.
# Each engine Lambda is configured by CDK through environment variables:
#   OLLAMA_ENGINE = engine id used in SNS filters / user configs ("llama", "qwen", ...)
#   OLLAMA_MODEL  = Ollama Cloud model tag (verify available tags on your account at
#                   https://ollama.com/search?c=cloud), e.g. "llama4:maverick-cloud",
#                   "qwen3.5:cloud"
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com/v1")
engine_type = os.environ.get("OLLAMA_ENGINE", "llama")
model = os.environ.get("OLLAMA_MODEL", "llama4:maverick")
request_timeout = 270  # seconds; engines have a 5-minute Lambda timeout

api_key = read_ssm_param(param_name="OLLAMA_API_KEY")
result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")


def process_command(input: str, context: UserContext) -> None:
    command = input.removeprefix(prefix="/").lower()
    logging.info(f"Processing command {command} for {context.user_id}")
    if "reset" in command:
        context.reset_conversation()
        logging.info(f"Conversation hass been reset for {context.user_id}")
        return
    logging.error(f"Unknown command {command}")


def ask(text: str, context: UserContext) -> str:
    if "/ping" in text:
        return "pong"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "stream": False,
        "max_tokens": 1024,
        "temperature": 0.7,
    }
    logging.info(f"Sending request to Ollama model '{model}' for {context.user_id}")
    response = requests.post(
        url=f"{OLLAMA_BASE_URL}/chat/completions",
        headers=headers,
        data=json.dumps(payload),
        timeout=request_timeout,
    )
    if not response.ok:
        logging.error(
            f"Ollama request failed: {response.status_code} {response.reason} {response.text}"
        )
        raise Exception(
            f"Ollama ({model}) request returned {response.status_code}: {response.text[:500]}"
        )
    data = response.json()
    content = data["choices"][0]["message"]["content"].strip()
    logging.info(f"Received {len(content)} chars from model '{model}'")
    return escape_markdown_v2(content)


def __process_payload(payload: Any, request_id: str) -> None:
    user_id = payload["user_id"]
    user_context = UserContext(
        user_id=f"{user_id}_{payload['chat_id']}",
        request_id=request_id,
        engine_id=engine_type,
        username=payload["username"],
    )
    text = payload["text"]
    if "command" in payload["type"]:
        process_command(input=text, context=user_context)
        return

    try:
        response = ask(text=text, context=user_context)
    except Exception as e:
        logging.error(
            f"Ollama engine '{engine_type}' failed for user {user_id}",
            exc_info=e,
        )
        response = escape_markdown_v2(str(e))

    user_context.save_conversation(
        conversation={"request": text, "response": response},
    )
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
