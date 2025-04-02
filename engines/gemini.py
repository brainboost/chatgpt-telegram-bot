import json
import logging
import re
import uuid
from typing import Any

import boto3
from google import genai
from google.genai import types

from .common_utils import encode_message, read_ssm_param
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "gemini"
model = "gemini-2.5-pro-exp-03-25"

bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")
_client = None
_generation_config = None


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
    file_path: str,
    context: UserContext,
) -> str:
    if "/ping" in text:
        return "pong"

    if context.conversation_id is None:
        context.conversation_id = str(uuid.uuid4())
    logging.info(f"conversation_id; '{context.conversation_id}'")
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=text),
            ],
        ),
    ]

    # if file_path:
    #     logging.info(f"Downloading image '{file_path}'")
    #     image = get_s3_file(file_path, bucket_name)
    #     imagePart = types.Part.from_bytes(Path(image).read_bytes())
    #     contents[0].parts.append(
    #         types.Part(
    #             inline_data={
    #                 "mime_type": "image/jpeg",
    #                 "data": Path(image).read_bytes(),
    #             }
    #         ),
    #     )
    response = _client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=_generation_config,
    )
    answer = ""
    for chunk in response:
        if len(chunk.parts) < 1 or "text" not in chunk.parts[0]:
            continue
        answer += chunk.parts[0].text
    return __as_markdown(answer)


def create() -> None:
    logging.info("Create chatbot instance")
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_ONLY_HIGH",
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_NONE",
        },
    ]
    global _generation_config
    _generation_config = types.GenerateContentConfig(
        temperature=1.2,
        top_p=0.85,
        max_output_tokens=65534,
        safety_settings=safety_settings,
        response_mime_type="text/plain",
        # response_mime_type="application/json",
    )
    global _client
    api_key = read_ssm_param(param_name="GEMINI_API_KEY")
    _client = genai.Client(api_key=api_key)


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

    if not (_client):
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
