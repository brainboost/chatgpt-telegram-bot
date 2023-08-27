import json
import logging
import os
import re
import uuid

import boto3
from Bard import Chatbot

from .common_utils import encode_message, read_ssm_param
from .conversation_history import ConversationHistory
from .engine_interface import EngineInterface

logging.basicConfig()
logging.getLogger().setLevel("INFO")


class BardEngine(EngineInterface):
    def __init__(self, chatbot: Chatbot) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatbot = chatbot
        self.history = ConversationHistory()
        self.esc_pattern = re.compile(f"([{re.escape(r'._-+#|{}!=()<>[]')}])")

    def reset_chat(self) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatbot.reset()

    def ask(self, text: str, userConfig: dict) -> str:
        if "/ping" in text:
            return "pong"
        response = self.chatbot.ask(message=text)
        # logging.info(response)
        self.conversation_id = response["conversation_id"]
        item = response["content"]
        item = self.as_markdown(item)
        try:
            self.history.write(
                conversation_id=self.conversation_id,
                request_id=response["response_id"],
                user_id=userConfig["user_id"],
                conversation=item,
            )
        except Exception as e:
            logging.error(
                f"conversation_id: {self.conversation_id}, error: {e}, item: {item}"
            )
            if "SNlM0e" in str(e):
                global instance
                instance = BardEngine.create()
                logging.info("a Bard instance is recreated")
        return item

    def close(self):
        self.chatbot.close()

    @property
    def engine_type(self):
        return "bard"

    @classmethod
    def create(cls) -> EngineInterface:
        socks_url = read_ssm_param(param_name="SOCKS5_URL")
        os.environ["all_proxy"] = socks_url
        token = read_ssm_param(param_name="BARD_TOKEN")
        psid_ts = read_ssm_param(param_name="BARD_1PSIDTS")
        chatbot = Chatbot(secure_1psid=token, secure_1psidts=psid_ts, timeout=40)
        return BardEngine(chatbot)

    def as_markdown(self, input: str) -> str:
        input = re.sub(r"(?<!\*)\*(?!\*)", "\\\\*", input)
        input = re.sub(r"\*{2,}", "*", input)
        return re.sub(self.esc_pattern, r"\\\1", input)


instance = BardEngine.create()
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")

# AWS SQS handler


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        logging.info(payload)
        response = instance.ask(payload["text"], payload["config"])
        payload["response"] = response
        logging.info(response)
        payload["response"] = encode_message(response)
        payload["engine"] = instance.engine_type
        logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
