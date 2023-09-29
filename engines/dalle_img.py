import json
import logging

import boto3
from BingImageCreator import ImageGen

from .common_utils import (
    encode_message,
    escape_markdown_v2,
    read_json_from_s3,
    read_ssm_param,
)
from .conversation_history import ConversationHistory

logging.basicConfig()
logging.getLogger().setLevel("INFO")


def create() -> ImageGen:
    s3_path = read_ssm_param(param_name="COOKIES_FILE")
    bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
    auth_cookies = read_json_from_s3(bucket_name, file_name)
    u = [x.get("value") for x in auth_cookies if x.get("name") == "_U"][0]
    srch = [x.get("value") for x in auth_cookies if x.get("name") == "SRCHHPGUSR"][0]
    return ImageGen(
        auth_cookie=u, auth_cookie_SRCHHPGUSR=srch, quiet=True, all_cookies=auth_cookies
    )


imageGen = create()
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")
history = ConversationHistory()


def sqs_handler(event, context):
    """AWS SQS event handler"""
    for record in event["Records"]:
        payload = json.loads(record["body"])
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
        payload["response"] = list
        user_id = payload["user_id"]
        conversation_id = f"{payload['chat_id']}_{user_id}_{payload['update_id']}"
        try:
            history.write(
                conversation_id=conversation_id,
                request_id=f'img_{payload["update_id"]}',
                user_id=user_id,
                conversation=payload,
            )
        except Exception as e:
            logging.error(
                f"conversation_id: {conversation_id}, error: {e}, item: {payload}"
            )

        logging.info(list)
        message = "\n".join(list)
        payload["response"] = encode_message(message)
        payload["engine"] = "Dall-E"
        # logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
