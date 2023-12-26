import json
import logging
from typing import Any

import boto3
from curl_cffi import requests

from .common_utils import (
    read_ssm_param,
)

logging.basicConfig()
logging.getLogger().setLevel("INFO")

base_url = "https://ideogram.ai"
browser_version = "chrome110"

post_task_url = f"{base_url}/api/images/sample"
retrieve_metadata_url = f"{base_url}/api/images/retrieve_metadata_request_id/"
get_images_url = f"{base_url}/api/images/direct/"

sqs = boto3.session.Session().client("sqs")
token = read_ssm_param(param_name="IDEOGRAM_TOKEN")
user_id = read_ssm_param(param_name="IDEOGRAM_USER")
channel_id = read_ssm_param(param_name="IDEOGRAM_CHANNEL")
headers = {
    "Cookie": f"session_cookie={token};",
    "Origin": base_url,
    "Referer": base_url + "/",
    "DNT": "1",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "TE": "trailers",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) \
        Gecko/20100101 Firefox/117.0",
}
ideogram_result_queue = sqs.get_queue_url(QueueName="Ideogram-Result-Queue")["QueueUrl"]


def request_images(prompt: str) -> str:
    payload = {
        "aspect_ratio": "square",
        "model_version": "V_0_2",
        "channel_id": channel_id,
        "prompt": prompt,
        "raw_or_fun": "raw",
        "speed": "slow",
        "style": "photo",
        "user_id": user_id,
        "variation_strength": 50
    }
    logging.info(payload)
    response = requests.post(
        url=post_task_url,
        headers=headers,
        data=json.dumps(payload),
        impersonate=browser_version,
    )
    if not response.ok:
        logging.error(response.text)
        raise Exception(f"Error response {str(response)}")

    response_body = response.json()
    logging.info(response_body)
    request_id = response_body["request_id"]
    if not request_id:
        raise Exception(f"Error {str(response_body)}")
    return request_id


def send_retrieving_event(event: object) -> None:
    logging.info(event)
    body = json.dumps(event)
    sqs.send_message(QueueUrl=ideogram_result_queue, MessageBody=body)

def __process_payload(payload: Any, request_id: str) -> None:
    prompt = payload["text"]
    if prompt is None or not prompt.strip():
        return

    result_id = request_images(prompt=prompt)
    payload["result_id"] = result_id
    payload["headers"] = headers
    payload["queue_url"] = ideogram_result_queue
    send_retrieving_event(payload)

def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        __process_payload(payload, request_id)

