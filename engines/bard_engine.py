import json
import logging
import re
import uuid
from typing import Any

import boto3
import requests
from bardapi import Bard

from .common_utils import encode_message, read_json_from_s3, read_ssm_param, save_to_s3
from .conversation_history import ConversationHistory
from .engine_interface import EngineInterface

logging.basicConfig()
logging.getLogger().setLevel("INFO")
headers = {
    "Host": "bard.google.com",
    "X-Same-Domain": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; "
    "rv:109.0) Gecko/20100101 Firefox/118.0",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "Origin": "https://bard.google.com",
    "Referer": "https://bard.google.com/",
}
cookie_names = [
    "__Secure-1PSID",
    "__Secure-1PSIDTS",
    "__Secure-1PSIDCC",
    "__Secure-1PAPISID",
]


class BardEngine(EngineInterface):
    def __init__(self, chatbot: Bard, cookies: Any) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatbot = chatbot
        self.cookies = cookies
        self.history = ConversationHistory()
        self.esc_pattern = re.compile(f"([{re.escape(r'._-+#|{}!=()<>[]')}])")

    def reset_chat(self) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())

    def ask(self, text: str, userConfig: dict) -> str:
        if "/ping" in text:
            return "pong"

        try:
            response = self.chatbot.get_answer(input_text=text)
        except Exception as e:
            logging.error("get_answer request failed, check cookies", exc_info=e)
            return "Sorry, requesting the Bard AI model failed"

        if not response or len(response) < 1 or "content" not in response:
            logging.error(response)
            raise Exception("Wrong Bard response, check logs")

        logging.info("Saving cookies")
        try:
            if self.chatbot.session and self.chatbot.session.cookies:
                for i in range(len(self.cookies)):
                    self.cookies[i]["value"] = self.chatbot.session.cookies.get(
                        name=self.cookies[i]["name"],
                        domain=self.cookies[i]["domain"],
                        path=self.cookies[i]["path"],
                    )
                BardEngine.save_cookies(self.cookies)

        except Exception as e:
            logging.error("Error saving session cookies", exc_info=e)

        self.conversation_id = self.chatbot.conversation_id
        logging.info(self.conversation_id)
        item = response["content"]
        answer = self.as_markdown(item)
        try:
            self.history.write(
                conversation_id=self.conversation_id,
                request_id=self.parent_id,
                user_id=userConfig["user_id"],
                conversation=item,
            )
        except Exception as e:
            logging.error(
                f"History write error, conversation_id: {self.conversation_id}, error: {e}, item: {item}",
                exc_info=e,
            )
        return answer

    def close(self):
        pass

    @property
    def engine_type(self):
        return "bard"

    @classmethod
    def read_cookies(cls) -> Any:
        cookies = read_json_from_s3(bucket_name, "bard-cookies.json")
        logging.info(f"Read {len(cookies)} cookies from s3")
        return cookies

    @classmethod
    def save_cookies(cls, cookies: Any) -> None:
        save_to_s3(bucket_name, "bard-cookies.json", cookies)
        logging.info(f"Saved {len(cookies)} cookies to s3")

    @classmethod
    def create(cls) -> EngineInterface:
        socks_url = read_ssm_param(param_name="SOCKS5_URL")
        proxies = {"https": socks_url, "http": socks_url}
        auth_cookies = BardEngine.read_cookies()
        psid = [
            x.get("value") for x in auth_cookies if x.get("name") == "__Secure-1PSID"
        ][0]
        session = requests.Session()
        session.headers = headers
        for i in range(len(auth_cookies)):
            if auth_cookies[i]["name"] in cookie_names:
                session.cookies.set(
                    name=auth_cookies[i]["name"],
                    value=auth_cookies[i]["value"],
                    domain=auth_cookies[i]["domain"],
                    path=auth_cookies[i]["path"],
                )
        logging.info(
            f"psid {psid}, proxy: {socks_url}, cookies: {len(session.cookies.items())}"
        )
        chatbot = Bard(
            token=psid,
            proxies=proxies,
            session=session,
            timeout=40,
        )
        return BardEngine(chatbot, auth_cookies)

    def as_markdown(self, input: str) -> str:
        input = re.sub(r"(?<!\*)\*(?!\*)", "\\\\*", input)
        input = re.sub(r"\*{2,}", "*", input)
        return re.sub(self.esc_pattern, r"\\\1", input)


bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")

# AWS SQS handler


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        logging.info(payload)
        instance = BardEngine.create()
        response = instance.ask(text=payload["text"], userConfig=payload["config"])
        payload["response"] = response
        logging.info(response)
        payload["response"] = encode_message(response)
        payload["engine"] = instance.engine_type
        logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
