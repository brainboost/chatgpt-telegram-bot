import asyncio
import json
import logging
import re
import uuid

import boto3
import common_utils as utils
from conversation_history import ConversationHistory
from EdgeGPT import Chatbot
from engine_interface import EngineInterface

logging.basicConfig()
logging.getLogger().setLevel("INFO")


class BingGpt(EngineInterface):
    def __init__(self, chatbot: Chatbot) -> None:
        self.remove_links_pattern = re.compile(r"\[\^\d+\^\]\s?")
        self.ref_link_pattern = re.compile(r"\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?")
        self.esc_pattern = re.compile(f"(?<!\|)([{re.escape(r'.-+#|{}!=()')}])(?!\|)")
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatbot = chatbot
        self.history = ConversationHistory()

    def reset_chat(self) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatbot.reset()

    def ask(self, text: str, userConfig: dict) -> str:
        if "/ping" in text:
            return "pong"
        style = userConfig.get("style", "creative")
        response = asyncio.run(self.chatbot.ask(prompt=text, conversation_style=style))
        item = response["item"]
        self.conversation_id = item["conversationId"]
        try:
            self.history.write(
                conversation_id=self.conversation_id,
                request_id=item["requestId"],
                user_id=userConfig["user_id"],
                conversation=item,
            )
        except Exception as e:
            logging.error(f"conversation_id: {self.conversation_id}, error: {e}")
        finally:
            logging.info(json.dumps(response, default=vars))

        if userConfig["plaintext"]:
            return self.read_plain_text(response)
        return self.read_markdown(response)

    def close(self):
        self.chatbot.close()

    @property
    def engine_type(self):
        return "bing"

    def read_cookies(self, s3_path) -> dict:
        s3 = boto3.client("s3")
        bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
        response = s3.get_object(Bucket=bucket_name, Key=file_name)
        file_content = response["Body"].read().decode("utf-8")
        return json.loads(file_content)

    def read_plain_text(self, response: dict) -> str:
        return re.sub(
            pattern=self.remove_links_pattern,
            repl="",
            string=response["item"]["messages"][1]["text"],
        )

    def read_markdown(self, response: dict) -> str:
        message = response["item"]["messages"][1]["adaptiveCards"][0]["body"][0]["text"]
        logging.info(message)
        return self.replace_references(text=message)

    def replace_references(self, text: str) -> str:
        ref_links = re.findall(pattern=self.ref_link_pattern, string=text)
        text = re.sub(pattern=self.ref_link_pattern, repl="", string=text)
        text = re.sub(pattern=self.esc_pattern, repl=r"\\\1", string=text)
        for link in ref_links:
            link_label = link[0]
            link_ref = link[1]
            inline_link = f" [\[{link_label}\]]({link_ref})"
            text = re.sub(
                pattern=rf"\[\^{link_label}\^\]\[\d+\]", repl=inline_link, string=text
            )
        return text

    @classmethod
    def create(cls) -> EngineInterface:
        s3_path = utils.read_ssm_param(param_name="COOKIES_FILE")
        bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
        chatbot = asyncio.run(
            Chatbot.create(cookies=utils.read_json_from_s3(bucket_name, file_name))
        )
        return BingGpt(chatbot)


bing = BingGpt.create()
results_queue = utils.read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")

# AWS SQS handler


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        logging.info(payload)
        response = bing.ask(payload["text"], payload["config"])
        logging.info(response)
        payload["response"] = utils.encode_message(response)
        payload["engine"] = bing.engine_type
        logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
