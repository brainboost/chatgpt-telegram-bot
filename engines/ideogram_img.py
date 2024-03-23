import json
import logging
from datetime import datetime
from typing import Any

import boto3
import jwt
from curl_cffi import requests

from .common_utils import (
    read_json_from_s3,
    read_ssm_param,
    save_to_s3,
)

logging.basicConfig()
logging.getLogger().setLevel("INFO")

base_url = "https://ideogram.ai"
browser_version = "chrome110"
user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0"
)
id_key = "AIzaSyBwq4bRiOapXYaKE-0Y46vLAw1-fzALq7Y"
tokens_file = "google_auth.json"
post_task_url = f"{base_url}/api/images/sample"

sqs = boto3.session.Session().client("sqs")
token = read_ssm_param(param_name="IDEOGRAM_TOKEN")
user_id = read_ssm_param(param_name="IDEOGRAM_USER")
# channel_id = read_ssm_param(param_name="IDEOGRAM_CHANNEL")
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
    "User-Agent": user_agent,
    # "Authorization": f"Bearer {channel_id}",
}
bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
ideogram_result_queue = sqs.get_queue_url(QueueName="Ideogram-Result-Queue")["QueueUrl"]


def is_expired(id_token: str) -> bool:
    try:
        claims = jwt.decode(jwt=id_token)
        exp = claims["exp"]
        now = datetime.now().timestamp()
        logging.info(f"exp:{exp}, now:{now}")
        return exp > now
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False


def refresh_tokens(refresh_token: str) -> dict:
    request_ref = "https://securetoken.googleapis.com/v1/token?key=" + id_key
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Client-Version": "Firefox/JsCore/9.23.0/FirebaseCore-web",
        "User-Agent": user_agent,
        "Origin": "https://ideogram.ai",
    }
    data = json.dumps({"grantType": "refresh_token", "refreshToken": refresh_token})
    response_object = requests.post(request_ref, headers=headers, data=data)
    response_object_json = response_object.json()
    tokens = {
        "user_id": response_object_json["user_id"],
        "id_token": response_object_json["id_token"],
        "refresh_token": response_object_json["refresh_token"],
    }
    save_to_s3(bucket_name=bucket_name, file_name=tokens_file, value=tokens)
    return tokens


def check_and_refresh_google_token() -> str:
    tokens = read_json_from_s3(bucket_name=bucket_name, file_name=tokens_file)
    if not tokens:
        error = f"Cannot read file '{tokens_file}' from the S3 bucket '{bucket_name}'. Put json with the field 'refresh_token' and save"
        logging.error(error)
        raise Exception(error)
    refresh_token = tokens.get("refresh_token", None)
    if not refresh_token:
        logging.error(f"No 'refresh_token' found in the {tokens_file}")
        return None
    id_token = tokens.get("id_token", None)
    if not id_token or is_expired(id_token):
        tokens = refresh_tokens(refresh_token=refresh_token)
    return tokens["id_token"]


def request_images(prompt: str) -> str:
    payload = {
        "aspect_ratio": "1:1",
        "model_version": "V_0_3",
        "use_autoprompt_option": "ON",
        "prompt": prompt,
        "raw_or_fun": "raw",
        "speed": "slow",
        "style": "photo",
        "user_id": user_id,
        "variation_strength": 50,
    }
    logging.info(payload)
    bearer = check_and_refresh_google_token()
    headers["Authorization"] = f"Bearer {bearer}"
    response = requests.post(
        url=post_task_url,
        headers=headers,
        data=json.dumps(payload),
        impersonate=browser_version,
        auth=("Bearer", bearer),
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
    if not prompt or not prompt.strip():
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
