import json
import logging

import boto3
import requests

from .common_utils import escape_markdown_v2, read_ssm_param
from .request_jobs import RequestJobs
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "llama"
model = "llama2-7b-chat"
callback_url = read_ssm_param(param_name="MONSTERAPI_CALLBACK_URL")
add_task_url = (
    f"https://api.monsterapi.ai/v1/generate/{model}?callbackURL={callback_url}"
)


def process_command(input: str, context: UserContext) -> None:
    command = input.removeprefix(prefix="/").lower()
    logging.info(f"Processing command {command} for {context.user_id}")
    if "reset" in command:
        context.reset_conversation()
        logging.info(f"Conversation hass been reset for {context.user_id}")
        return
    logging.error(f"Unknown command {command}")


def ask(
    text: str,
    context: UserContext,
) -> str:
    payload = {
        "prompt": text,
        "max_length": 512,
    }
    logging.info(add_task_url)
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "accept": "application/json",
    }
    response = requests.post(
        url=add_task_url,
        headers=headers,
        data=json.dumps(payload),
    )
    response_body = response.json()
    logging.info(response_body)
    if not response.ok:
        err_message = {
            "response": escape_markdown_v2(response["message"]),
            "engine": engine_type,
        }
        sqs.send_message(QueueUrl=results_queue, MessageBody=err_message)
        return

    process_id = response_body["process_id"]
    logging.info(process_id)
    return process_id


token = read_ssm_param(param_name="MONSTERAPI_TOKEN")
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")


def sqs_handler(event, context):
    """AWS SQS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["body"])
        user_id = payload["user_id"]
        user_context = UserContext(
            user_id=f"{user_id}_{payload['chat_id']}",
            request_id=request_id,
            engine_id=engine_type,
            username=payload["username"],
        )
        question = payload["text"]
        if "/ping" in question:
            payload["response"] = "pong"
            sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
            return

        if "command" in payload["type"]:
            process_command(input=question, context=user_context)
            return

        process_id = ask(text=question, context=user_context)
        req_resp = RequestJobs(request_id=process_id, engine_id=engine_type)
        req_resp.save(
            context={
                "user_id": user_id,
                "chat_id": payload["chat_id"],
                "message_id": payload["message_id"],
                "update_id": payload["update_id"],
                "username": payload["username"],
                "text": question,
            },
        )
        user_context.conversation_id = process_id
        user_context.parent_id = process_id
        user_context.save_context()
