import json
import logging
import re
from collections import deque

import boto3
from revChatGPT.V1 import Chatbot

# from revChatGPT.V3 import Chatbot
from .common_utils import encode_message, read_ssm_param
from .conversation_history import ConversationHistory
from .engine_interface import EngineInterface

logging.basicConfig()
logging.getLogger().setLevel("INFO")


class ChatGpt(EngineInterface):
    def __init__(self, chatbot: Chatbot) -> None:
        self.remove_links_pattern = re.compile(r"\[\^\d+\^\]\s?")
        self.ref_link_pattern = re.compile(r"\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?")
        self.esc_pattern = re.compile(
            f"(?<!\|)([{re.escape(r'._-+#|{}!=()<>')}])(?!\|)"
        )
        self.conversation_id = None
        self.parent_id = None
        self.chatbot = chatbot
        self.history = ConversationHistory()

    def reset_chat(self) -> None:
        self.conversation_id = None
        self.parent_id = None
        self.chatbot.reset()

    def close(self):
        self.chatbot.clear_conversations()

    def ask(self, text: str, userConfig: dict) -> str:
        if "/ping" in text:
            return "pong"

        response = deque(
            self.chatbot.ask(
                prompt=text,
                conversation_id=self.conversation_id,
                parent_id=self.parent_id,
                auto_continue=(self.conversation_id is not None),
            ),
            maxlen=1,
        )[-1]
        message = response["message"]
        self.parent_id = response["parent_id"]
        self.conversation_id = response["conversation_id"]
        # logging.info(response["model"])
        return re.sub(pattern=self.esc_pattern, repl=r"\\\1", string=message)

    @property
    def engine_type(self):
        return "chatgpt"

    @classmethod
    def create(cls) -> EngineInterface:
        gpt_token = read_ssm_param(param_name="GPT_TOKEN")
        # api_key = read_ssm_param("OPENAI_API_KEY")
        chatbot = Chatbot(config={"access_token": gpt_token})
        # chatbot = Chatbot(api_key=api_key)
        return ChatGpt(chatbot)


instance = ChatGpt.create()
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")

# AWS SQS handler


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        response = instance.ask(payload["text"], payload["config"])
        payload["response"] = response
        payload["response"] = encode_message(response)
        payload["engine"] = instance.engine_type
        # logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
