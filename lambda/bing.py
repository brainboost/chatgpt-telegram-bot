import uuid
import logging
import boto3
import json
from EdgeGPT import Chatbot

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

    async def ask(self, text) -> str:
        try:
            response = await self.chatbot.ask(prompt=text)

        except Exception as e:
            logging.error(e)
            message = f"{e}"
        else:
            logging.info(response)
            message = response["item"]["messages"][1]["adaptiveCards"][0]["body"][0][
                "text"
            ]
        return message

    def read_cookies(self, s3_path) -> dict:
        s3 = boto3.client("s3")
        bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
        response = s3.get_object(Bucket=bucket_name, Key=file_name)
        file_content = response["Body"].read().decode("utf-8")
        return json.loads(file_content)
