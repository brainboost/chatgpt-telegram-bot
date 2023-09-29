import json
import logging
from typing import Optional

import boto3
from curl_cffi import requests

from .common_utils import (
    encode_message,
    read_ssm_param,
)
from .conversation_history import ConversationHistory

logging.basicConfig()
logging.getLogger().setLevel("INFO")

threshold_img_quality = 1024
browser_version = "chrome110"
retrieve_metadata_url = "https://ideogram.ai/api/images/retrieve_metadata_request_id/"
get_images_url = "https://ideogram.ai/api/images/direct/"

results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")
history = ConversationHistory()


def retrieve_images(config: any) -> Optional[str]:
    logging.info(config)
    request_id = config["request_id"]
    if not request_id:
        raise Exception("Cannot get request_id")

    response = requests.get(
        url=retrieve_metadata_url + request_id,
        headers=config["headers"],
        impersonate=browser_version,
    )
    if not response.ok:
        logging.info(response)
        raise Exception(f"Cannot retrieve images for request_id {request_id}")

    resp_obj = response.json()
    if "resolution" not in resp_obj or resp_obj["resolution"] < threshold_img_quality:
        logging.info(f"Republishing for {request_id} to achieve delay ...")
        queue_url = config["queue_url"]
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(config))
        return None

    list = []
    for response in resp_obj["responses"]:
        list.append(get_images_url + response["response_id"])

    logging.info(list)
    message = "\n".join(list)
    return message


def sqs_handler(event, context):
    """AWS SQS event handler"""
    for record in event["Records"]:
        payload = json.loads(record["body"])
        message = retrieve_images(config=payload)
        if not message:
            return

        payload["response"] = encode_message(message)
        payload["engine"] = "Ideogram"
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
        # logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
