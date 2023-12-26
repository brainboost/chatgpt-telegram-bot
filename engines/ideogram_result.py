import json
import logging
from typing import Optional

import boto3
from curl_cffi import requests

from .common_utils import (
    encode_message,
    read_ssm_param,
)
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "ideogram"
threshold_img_quality = 1024
browser_version = "chrome110"
retrieve_metadata_url = "https://ideogram.ai/api/images/retrieve_metadata_request_id/"
get_images_url = "https://ideogram.ai/api/images/direct/"

result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")
sqs = boto3.session.Session().client("sqs")


def retrieve_images(payload: dict) -> Optional[str]:
    logging.info(payload)
    result_id = payload["result_id"]
    if not result_id:
        raise Exception("Cannot get result_id")

    response = requests.get(
        url=retrieve_metadata_url + result_id,
        headers=payload["headers"],
        impersonate=browser_version,
    )
    if not response.ok:
        logging.info(response)
        raise Exception(f"Cannot retrieve images for result_id {result_id}")

    resp_obj = response.json()
    if "resolution" not in resp_obj or resp_obj["resolution"] < threshold_img_quality:
        logging.info(f"Republishing results {result_id} to achieve delay...")
        queue_url = payload["queue_url"]
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(payload))
        return None

    list = []
    for response in resp_obj["responses"]:
        list.append(get_images_url + response["response_id"])

    logging.info(list)
    message = "\n".join(list)
    return message


def sqs_handler(event, context):
    """AWS SQS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["body"])
        message = retrieve_images(payload=payload)
        if not message:
            return

        payload["response"] = encode_message(message)
        payload["engine"] = engine_type
        user_id = payload["user_id"]
        result_id = payload["result_id"]
        user_context = UserContext(
            user_id=f"{user_id}_{payload['chat_id']}",
            request_id=result_id,
            engine_id=engine_type,
            username=payload["username"],
        )
        user_context.conversation_id = result_id
        try:
            user_context.save_conversation(
                conversation=payload,
            )
        except Exception as e:
            logging.error(
                f"Saving conversation error. User_id: {user_id}_{payload['chat_id']}, item: {payload}",
                exc_info=e,
            )
        sns.publish(TopicArn=result_topic, Message=json.dumps(payload))
