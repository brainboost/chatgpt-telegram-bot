import json
import logging
import os
import re
import textwrap
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
import google.generativeai as genai
from google.ai.generativelanguage import (
    Part,
)
from google.generativeai.client import (
    _ClientManager,
)

from .common_utils import encode_message, read_ssm_param
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "gemini"
safety_level = "BLOCK_ONLY_HIGH"

bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")
_model: genai.GenerativeModel = None
_vision_model = None


def process_command(input: str, context: UserContext) -> None:
    command = input.removeprefix(prefix="/").lower()
    logging.info(f"Processing command {command} for {context.user_id}")
    if "reset" in command:
        context.reset_conversation()
        logging.info(f"Conversation hass been reset for {context.user_id}")
        return
    logging.error(f"Unknown command {command}")


def get_image(s3_uri: str) -> str:
    file_name = urlparse(s3_uri).path.split("/")[-1]
    logging.info(f"Downloading file 'att/{file_name}' from s3 bucket {bucket_name}")
    tmp_file = f"/tmp/{file_name}"
    session = boto3.session.Session()
    session.client("s3").download_file(
        Bucket=bucket_name,
        Key=f"att/{file_name}",
        Filename=tmp_file,
    )
    if not (img := Path(tmp_file)).exists():
        logging.error(
            f"File {tmp_file} does not exist. Problem to download from s3 '{s3_uri}'"
        )
        raise FileNotFoundError(f"Could not find image: {img}")
    return tmp_file


def ask(
    text: str,
    file_path: str,
    context: UserContext,
) -> str:
    if "/ping" in text:
        return "pong"

    if context.conversation_id is None:
        context.conversation_id = str(uuid.uuid4())
    logging.info(f"conversation_id; '{context.conversation_id}'")
    if file_path:
        logging.info(f"Downloading image '{file_path}'")
        image = get_image(file_path)
        response = _vision_model.generate_content(
            [
                Part(
                    inline_data={
                        "mime_type": "image/jpeg",
                        "data": Path(image).read_bytes(),
                    }
                ),
                Part(text=text),
            ],
            stream=True,
        )
    else:
        response = _model.generate_content(
            # [Content.from_json(content) for content in conversation],
            text,
            stream=True,
        )
    answer = ""
    for chunk in response:
        if len(chunk.parts) < 1 or "text" not in chunk.parts[0]:
            continue
        answer += chunk.parts[0].text
    return __as_markdown(answer)


def create() -> None:
    """Initialize model API https://ai.google.dev/api"""

    logging.info("Create chatbot instance")
    proxy_url = read_ssm_param(param_name="SOCKS5_URL")
    os.environ["http_proxy"] = proxy_url
    logging.info(f"Initializing Google AI module with proxy '{proxy_url}'")
    generation_config = {
        "temperature": 0.8,
        "top_p": 1,
        "top_k": 32,
        "max_output_tokens": 4096,
    }
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": safety_level},
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": safety_level,
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": safety_level,
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": safety_level,
        },
    ]
    global _model
    _model = genai.GenerativeModel(
        model_name="gemini-pro",
        generation_config=generation_config,
        safety_settings=safety_settings,
    )
    global _vision_model
    _vision_model = genai.GenerativeModel(
        model_name="gemini-pro-vision",
        generation_config=generation_config,
        safety_settings=safety_settings,
    )
    api_key = read_ssm_param(param_name="GEMINI_API_KEY")
    client_manager = _ClientManager()
    client_manager.configure(api_key=api_key)
    _model._client = client_manager.get_default_client("generative")
    _vision_model._client = client_manager.get_default_client("generative")


def __as_markdown(input: str) -> str:
    input = re.sub(r"(?<!\*)\*(?!\*)", "\\\\*", input)
    input = re.sub(r"\*{2,}", "*", input)
    esc_pattern = re.compile(f"([{re.escape(r'._-+#|{}!=()<>[]')}])")
    return re.sub(esc_pattern, r"\\\1", input)


def __process_payload(payload: Any, request_id: str) -> None:
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

    if not (_model and _vision_model):
        create()

    response = ask(
        text=payload["text"],
        file_path=payload.get("file", None),
        context=user_context,
    )
    user_context.save_conversation(
        conversation={"request": payload["text"], "response": response},
    )
    payload["response"] = encode_message(response)
    payload["engine"] = engine_type
    # logging.info(payload)
    sns.publish(TopicArn=result_topic, Message=json.dumps(payload))


def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        __process_payload(payload, request_id)
