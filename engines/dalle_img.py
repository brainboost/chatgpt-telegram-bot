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
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "dall-e"


def create() -> ImageGen:
    bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
    auth_cookies = read_json_from_s3(bucket_name, "bing-cookies.json")
    u = [x.get("value") for x in auth_cookies if x.get("name") == "_U"][0]
    srch = [x.get("value") for x in auth_cookies if x.get("name") == "SRCHHPGUSR"][0]
    return ImageGen(
        auth_cookie=u, auth_cookie_SRCHHPGUSR=srch, quiet=True, all_cookies=auth_cookies
    )


imageGen = create()
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")


def sqs_handler(event, context):
    """AWS SQS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
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
        user_context = UserContext(
            user_id=f"{user_id}_{payload['chat_id']}",
            request_id=request_id,
            engine_id=engine_type,
            username=payload["username"],
        )
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
        # logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
