import asyncio
import json
import logging
import time

import boto3

from .common_utils import encode_message, escape_markdown_v2, read_ssm_param
from .conversation_history import ConversationHistory

logging.basicConfig()
logging.getLogger().setLevel("INFO")
fetch_url = "https://api.monsterapi.ai/v1/status/"

results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
api_key = read_ssm_param(param_name="MONSTER_API_KEY")
token = read_ssm_param(param_name="MONSTER_TOKEN")
sqs = boto3.session.Session().client("sqs")
history = ConversationHistory()

headers = {
    # "x-api-key": api_key,
    "authorization": f"Bearer {token}",
    "accept": "application/json",
}


def callback_handler(event, context) -> None:
    logging.info(event)
    try:
        return asyncio.get_event_loop().run_until_complete(_process_response(event))
    except Exception as e:
        logging.error(str(e))


async def _process_response(event):
    body = json.loads(event["body"])
    status = body["status"]
    # status  [IN_QUEUE, IN_PROGRESS, COMPLETED, FAILED]
    if status == "IN_PROGRESS" or status == "IN_QUEUE":
        return
    elif status == "COMPLETED":
        process_id = body["process_id"]
        logging.info(process_id)
        item = body["result"]
        logging.info(item)
        try:
            hist = history.read(
                conversation_id=process_id,
                request_id=process_id,
            )
        except Exception as e:
            logging.error(f"process_id: {process_id}, error: {e}, item: {item}")
    else:
        error = body["result"]["errorMessage"]
        item = f"Error: {error}"
        logging.error(f"{item}, process_id: {process_id}, Response: {body}")

    config = hist["config"]
    # logging.info(hist)
    payload = {
        "type": "text",
        "text": hist["data"],
        "engine": "llama",
        "timestamp": int(time.time()),
        "user_id": config["user_id"],
        "update_id": config["update_id"],
        "message_id": config["message_id"],
        "chat_id": config["chat_id"],
        "config": config,
    }
    text = escape_markdown_v2(item["text"])
    payload["response"] = encode_message(text)
    # logging.info(config)
    try:
        history.write(
            conversation_id=process_id,
            request_id=process_id,
            user_id=payload["user_id"],
            conversation=payload,
        )
    except Exception as e:
        logging.error(
            f"conversation_id: {process_id}, request_id:{process_id} error: {e}, item: {payload}"
        )
    sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
