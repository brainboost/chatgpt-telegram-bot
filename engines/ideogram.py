import json
import logging
import time

import boto3
import requests

from .common_utils import (
    encode_message,
    read_ssm_param,
)
from .conversation_history import ConversationHistory

logging.basicConfig()
logging.getLogger().setLevel("INFO")
post_task_url = "https://ideogram.ai/api/images/sample"
retrieve_metadata_url = "https://ideogram.ai/api/images/retrieve_metadata_request_id/"
get_images_url = "https://ideogram.ai/api/images/direct/"

results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")
history = ConversationHistory()
token = read_ssm_param(param_name="IDEOGRAM_TOKEN")
user_id = read_ssm_param(param_name="IDEOGRAM_USER")
channel_id = read_ssm_param(param_name="IDEOGRAM_CHANNEL")
headers = {
    "Cookie": f"session_cookie={token}",
    "Origin": "https://ideogram.ai",
    "Referer": "https://ideogram.ai/",
    "DNT": "1",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "TE": "trailers",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0",
}


def get_images(prompt: str, userConfig: dict) -> list:
    list = []
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
    )
    if not response.ok:
        logging.error(response.text)
        raise Exception(f"Error response {str(response)}")

    response_body = response.json()
    logging.info(response_body)
    request_id = response_body["request_id"]
    caption = response_body["caption"]
    list = check_metadata(request_id=request_id)
    try:
        history.write(
            conversation_id=user_id,
            request_id=request_id,
            user_id=userConfig["user_id"],
            conversation={
                "caption": caption,
                "images": json.dumps(list),
                "config": userConfig,
            },
        )
    except Exception as e:
        logging.error(
            f"request_id: {request_id}, user_id: {user_id}, error: {e}, item: {response_body}"
        )
    return list


def check_metadata(request_id: str) -> list:
    resp = []
    while not resp:
        response = requests.get(
            url=retrieve_metadata_url + request_id,
            headers=headers,
        )
        logging.info(response)
        if not response.ok:
            resp.append(str(response))
            logging.error(response.text)
            return resp

        resp_obj = response.json()
        if "resolution" not in resp_obj or resp_obj["resolution"] < 1024:
            time.sleep(2)
            continue

        for response in resp_obj["responses"]:
            resp.append(get_images_url + response["response_id"])
    return resp


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        prompt = payload["text"]
        config = payload["config"]
        logging.info(config)
        list = []
        if prompt is not None and prompt.strip():
            list = get_images(prompt=prompt, userConfig=config)
        else:
            # for testing purposes
            list = [
                "https://picsum.photos/200#1",
                "https://picsum.photos/200#2",
                "https://picsum.photos/200#3",
                "https://picsum.photos/200#4",
            ]
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
        payload["engine"] = "Ideogram"
        # logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
