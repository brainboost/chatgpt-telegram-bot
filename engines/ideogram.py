import json
import logging

import boto3
from curl_cffi import requests

from .common_utils import (
    read_ssm_param,
)
from .conversation_history import ConversationHistory

logging.basicConfig()
logging.getLogger().setLevel("INFO")

base_url = "https://ideogram.ai"
browser_version = "chrome110"

post_task_url = f"{base_url}/api/images/sample"
retrieve_metadata_url = f"{base_url}/api/images/retrieve_metadata_request_id/"
get_images_url = f"{base_url}/api/images/direct/"

results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
ideogram_result_queue = results_queue.replace(
    results_queue.split("/")[-1], "Ideogram-Result-Queue"
)
sqs = boto3.session.Session().client("sqs")
history = ConversationHistory()
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


def request_images_generation(prompt: str) -> str:
    payload = {
        "aspect_ratio": "square",
        "channel_id": channel_id,
        "prompt": prompt,
        "raw_or_fun": "raw",
        "speed": "slow",
        "style": "photo",
        "user_id": user_id,
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


def sqs_handler(event, context):
    """AWS SQS event handler"""
    for record in event["Records"]:
        payload = json.loads(record["body"])
        prompt = payload["text"]
        if prompt is None or not prompt.strip():
            return
        request_id = request_images_generation(prompt=prompt)
        payload["request_id"] = request_id
        payload["headers"] = headers
        payload["queue_url"] = ideogram_result_queue
        send_retrieving_event(payload)
