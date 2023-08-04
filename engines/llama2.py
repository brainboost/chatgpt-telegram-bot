import json
import logging
import re
from time import sleep

import boto3
import requests

from .common_utils import encode_message, read_ssm_param
from .conversation_history import ConversationHistory
from .engine_interface import EngineInterface

logging.basicConfig()
logging.getLogger().setLevel("INFO")

add_task_url = "https://api.monsterapi.ai/apis/add-task"
fetch_url = "https://api.monsterapi.ai/apis/task-status"


class LLama2(EngineInterface):
    def __init__(self, apiKey: str, token: str) -> None:
        self.remove_links_pattern = re.compile(r"\[\^\d+\^\]\s?")
        self.ref_link_pattern = re.compile(r"\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?")
        self.esc_pattern = re.compile(f"(?<!\|)([{re.escape(r'.-+#|{}!=()<>')}])(?!\|)")
        self.process_id = None
        self.history = ConversationHistory()
        self.headers = {
            "x-api-key": apiKey,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def reset_chat(self) -> None:
        self.process_id = None
        self.chatbot.reset()

    def close(self):
        self.chatbot.close()

    @property
    def engine_type(self):
        return "llama"

    def ask(self, text: str, userConfig: dict) -> str:
        if "/ping" in text:
            return "pong"
        data = {
            "model": "llama2-7b-chat",
            "data": {
                "prompt": text,
                "top_k": 10,
                "top_p": 0.9,
                "temp": 0.1,
                "max_length": 512,
                "beam_size": 1,
            },
        }
        response = requests.post(
            add_task_url, headers=self.headers, data=json.dumps(data)
        )
        # logging.info(response.json())
        self.process_id = response.json()["process_id"]
        status = None
        while True:
            response = requests.post(
                fetch_url,
                headers=self.headers,
                data=json.dumps(
                    {
                        "process_id": self.process_id,
                    }
                ),
            ).json()

            status = response["response_data"]["status"]
            if status not in ("COMPLETED", "FAILED"):
                sleep(2)
            else:
                break

        if status == "COMPLETED":
            item = response["response_data"]["result"]["text"]
            logging.info(response)
            try:
                self.history.write(
                    conversation_id=self.process_id,
                    request_id=self.process_id,
                    user_id=userConfig["user_id"],
                    conversation=response,
                )
            except Exception as e:
                logging.error(
                    f"process_id: {self.process_id}, error: {e}, item: {item}"
                )
        else:
            error = response["response_data"]["result"]["errorMessage"]
            item = f"Error: {error}"
            logging.error(f"{item}, Request: {data}, Response: {response}")

        return re.sub(pattern=self.esc_pattern, repl=r"\\\1", string=item)

    @classmethod
    def create(cls) -> EngineInterface:
        api_key = read_ssm_param(param_name="MONSTER_API_KEY")
        token = read_ssm_param(param_name="MONSTER_TOKEN")
        # socks_url = read_ssm_param(param_name="SOCKS5_URL")
        # os.environ["all_proxy"] = socks_url
        return LLama2(apiKey=api_key, token=token)


instance = LLama2.create()
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")

# AWS SQS handler


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        response = instance.ask(payload["text"], payload["config"])
        payload["response"] = encode_message(response)
        payload["engine"] = instance.engine_type
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
