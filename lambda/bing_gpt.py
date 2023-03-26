import json
import logging
import uuid

import boto3
from EdgeGPT import Chatbot
from telegram import constants, helpers

logging.basicConfig()
logging.getLogger().setLevel("INFO")


class BingGpt:
    def __init__(self) -> None:
        _ssm_client = boto3.client(service_name="ssm")
        s3_path = _ssm_client.get_parameter(Name="COOKIES_FILE")["Parameter"]["Value"]
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        cookies = self.read_cookies(s3_path)
        self.chatbot = Chatbot(cookies=cookies)

    def reset_chat(self) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())

    async def ask(self, text, userConfig: dict) -> str:
        response = await self.chatbot.ask(prompt=text)
        # logging.info(json.dumps(response, default=vars))
        if "plaintext" in userConfig is True:
            return self.read_plain_text(response)
        return self.read_markdown(response)

    async def close(self):
        await self.chatbot.close()

    def read_cookies(self, s3_path) -> dict:
        s3 = boto3.client("s3")
        bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
        response = s3.get_object(Bucket=bucket_name, Key=file_name)
        file_content = response["Body"].read().decode("utf-8")
        return json.loads(file_content)

    def read_plain_text(self, response: dict) -> str:
        return response["item"]["messages"][1]["text"]

    def read_markdown(self, response: dict) -> str:
        message = response["item"]["messages"][1]["adaptiveCards"][0]["body"][0]["text"]
        return helpers.escape_markdown(message, 2, constants.ParseMode.MARKDOWN_V2)

