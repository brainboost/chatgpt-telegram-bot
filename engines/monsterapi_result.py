import json
import logging
import time

import boto3

from .common_utils import encode_message, escape_markdown_v2, read_ssm_param
from .request_jobs import RequestJobs
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "llama"
fetch_url = "https://api.monsterapi.ai/v1/status/"

results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
token = read_ssm_param(param_name="MONSTERAPI_TOKEN")
sqs = boto3.session.Session().client("sqs")

headers = {
    "authorization": f"Bearer {token}",
    "accept": "application/json",
}


def callback_handler(event, context) -> None:
    """AWS Lambda event handler"""
    body = json.loads(event["body"])
    process_id = body["process_id"]
    status = body["status"]
    #   [IN_QUEUE, IN_PROGRESS, COMPLETED, FAILED]
    if status == "IN_PROGRESS" or status == "IN_QUEUE":
        return
    elif status == "COMPLETED":
        logging.info(f"Process ID: {process_id}")
        item = body["result"]
        # logging.info(item)
    else:
        error = body["result"]["errorMessage"]
        item = {"text": f"Error: {error}"}
        logging.error(
            f"Request failed with error {error}, process_id: {process_id}, Response: {body}"
        )

    req_resp = RequestJobs(request_id=process_id, engine_id=engine_type)
    config = req_resp.read() or {
        "text": f"Request {process_id} for '{engine_type}' is not available"
    }
    # logging.info(config)
    user_id = config.get("user_id", None)
    payload = {
        "type": "text",
        "text": config["text"],
        "engine": engine_type,
        "timestamp": int(time.time()),
        "user_id": user_id,
        "update_id": config.get("update_id", None),
        "message_id": config.get("message_id", None),
        "chat_id": config.get("chat_id", None),
    }
    text = escape_markdown_v2(item["text"])
    payload["response"] = encode_message(text)
    if user_id:
        try:
            user_context = UserContext(
                user_id=f"{config['user_id']}_{config['chat_id']}",
                engine_id=engine_type,
                request_id=process_id,
                username=config["username"],
            )
            user_context.conversation_id = process_id
            user_context.parent_id = process_id
            user_context.save_conversation(
                conversation={"request": config["text"], "response": item["text"]}
            )
        except Exception as e:
            logging.error(
                f"Error on saving conversation_id: {process_id}, request_id:{process_id}, payload: {payload}",
                exc_info=e,
            )
    logging.info(payload)
    sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
