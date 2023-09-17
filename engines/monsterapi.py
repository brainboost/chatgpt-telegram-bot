import json
import logging
import re

import boto3
import requests

from .common_utils import escape_markdown_v2, read_ssm_param
from .conversation_history import ConversationHistory
from .engine_interface import EngineInterface

logging.basicConfig()
logging.getLogger().setLevel("INFO")

model = "llama2-7b-chat"
callback_url = read_ssm_param(param_name="MONSTERAPI_CALLBACK_URL")
add_task_url = (
    f"https://api.monsterapi.ai/v1/generate/{model}?callbackURL={callback_url}"
)


class LLama2(EngineInterface):
    def __init__(self, apiKey: str, token: str) -> None:
        self.remove_links_pattern = re.compile(r"\[\^\d+\^\]\s?")
        self.ref_link_pattern = re.compile(r"\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?")
        self.history = ConversationHistory()
        self.headers = {
            # "x-api-key": apiKey,
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": "application/json",
        }

    def reset_chat(self) -> None:
        self.chatbot.reset()

    def close(self):
        self.chatbot.close()

    @property
    def engine_type(self):
        return "llama"

    def ask(self, text: str, userConfig: dict) -> str:
        payload = {
            "prompt": text,
            "max_length": 512,
        }
        logging.info(add_task_url)
        response = requests.post(
            url=add_task_url,
            headers=self.headers,
            data=json.dumps(payload),
        )
        response_body = response.json()
        logging.info(response_body)
        if not response.ok:
            err_message = {
                "response": escape_markdown_v2(response["message"]),
                "engine": self.engine_type(),
            }
            sqs.send_message(QueueUrl=results_queue, MessageBody=err_message)
            return

        process_id = response_body["process_id"]
        try:
            self.history.write(
                conversation_id=process_id,
                request_id=process_id,
                user_id=userConfig["user_id"],
                conversation={
                    "data": text,
                    "config": userConfig,
                },
            )
        except Exception as e:
            logging.error(
                f"process_id: {process_id}, conversation_id: {self.conversation_id}, error: {e}, item: {response_body}"
            )

        return process_id

    @classmethod
    def create(cls) -> EngineInterface:
        api_key = read_ssm_param(param_name="MONSTER_API_KEY")
        token = read_ssm_param(param_name="MONSTER_TOKEN")
        return LLama2(apiKey=api_key, token=token)


instance = LLama2.create()
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")

# AWS SQS handler


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        config = payload["config"]
        config["user_id"] = payload["user_id"]
        config["chat_id"] = payload["chat_id"]
        config["message_id"] = payload["message_id"]
        config["update_id"] = payload["update_id"]
        prompt = payload["text"]
        if "/ping" in prompt:
            payload["response"] = "pong"
            sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
            return

        response = instance.ask(text=prompt, userConfig=config)
        logging.info(response)
