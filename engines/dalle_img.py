import json
import logging
from typing import Any

import boto3
from BingImageCreator import ImageGen

from .common_utils import (
    encode_message,
    escape_markdown_v2,
    read_json_from_s3,
    read_ssm_param,
)
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "dall-e"


def create() -> ImageGen:
    bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
    auth_cookies = read_json_from_s3(bucket_name, "bing-cookies.json")
    u = [x.get("value") for x in auth_cookies if x.get("name") == "_U"][0]
    # srch = [
    #     x.get("value")
    #     for x in auth_cookies
    #     if x.get("name") == "SRCHHPGUSR" and x.get("path") == "/images"
    # ][0]
    return ImageGen(
        auth_cookie=u,
        # auth_cookie_SRCHHPGUSR=srch,
        quiet=False,
        all_cookies=auth_cookies,
    )


imageGen = create()
result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")


def __process_payload(payload: Any, request_id: str) -> None:
    prompt = payload["text"]
    list = []
    if prompt is None or not prompt.strip():
        return
    try:
        list = imageGen.get_images(prompt)
    except Exception as e:
        if "prompt has been blocked" in str(e):
            message = escape_markdown_v2(str(e))
            list = [message]
        else:
            logging.error(e)
            logging.info(payload)
            logging.info(imageGen.session.__dict__)

    payload["response"] = list
    user_id = payload["user_id"]
    user_context = UserContext(
        user_id=f"{user_id}_{payload['chat_id']}",
        request_id=request_id,
        engine_id=engine_type,
        username=payload["username"],
    )
    user_context.conversation_id = request_id
    try:
        user_context.save_conversation(
            conversation=payload,
        )
    except Exception as e:
        logging.error(
            f"Saving conversation error. User_id: {user_id}_{payload['chat_id']}, item: {payload}",
            exc_info=e,
        )
    logging.info(list)
    message = "\n".join(list)
    payload["response"] = encode_message(message)
    payload["engine"] = engine_type
    sns.publish(TopicArn=result_topic, Message=json.dumps(payload))


def sqs_handler(event, context):
    """AWS SQS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["body"])
        __process_payload(payload, request_id)


def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        __process_payload(payload, request_id)
