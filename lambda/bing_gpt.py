import json
import logging
import re
import uuid

import boto3
import markdown
from EdgeGPT import Chatbot

logging.basicConfig()
logging.getLogger().setLevel("INFO")

class BingGpt:
    ref_link_pattern = re.compile(r"\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?")
    esc_pattern = re.compile(f"(?<!\|)([{re.escape(r'.-+#|{}!=()')}])(?!\|)")

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

    async def ask(self, text: str, userConfig: dict) -> str:
        response = await self.chatbot.ask(prompt=text)
        logging.info(json.dumps(response, default=vars))
        return response

    async def close(self):
        await self.chatbot.close()

    def read_cookies(self, s3_path) -> dict:
        s3 = boto3.client("s3")
        bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
        response = s3.get_object(Bucket=bucket_name, Key=file_name)
        file_content = response["Body"].read().decode("utf-8")
        return json.loads(file_content)

    @classmethod
    def read_plain_text(cls, response: dict) -> str:
        return BingGpt.remove_links(text=response["item"]["messages"][1]["text"])

    @classmethod
    def read_markdown(cls, response: dict) -> str:
        message = response["item"]["messages"][1]["adaptiveCards"][0]["body"][0]["text"]
        return BingGpt.replace_references(text=message)

    @classmethod
    def read_html(cls, response: dict) -> str:
        message = response["item"]["messages"][1]["adaptiveCards"][0]["body"][0]["text"]
        text = BingGpt.replace_references(text=message)
        return markdown.markdown(text=text)
   
    @classmethod
    def replace_references(cls, text: str) -> str:
        ref_links = re.findall(pattern=cls.ref_link_pattern, string=text)
        text = re.sub(pattern=cls.ref_link_pattern, repl="", string=text)
        text = BingGpt.escape_markdown_v2(text=text)
        for link in ref_links:
            link_label = link[0]
            link_ref = link[1]
            inline_link = f" [\[{link_label}\]]({link_ref})"
            text = re.sub(pattern=rf"\[\^{link_label}\^\]\[\d+\]", 
                repl=inline_link, string=text)
        return text

    @classmethod
    def remove_links(cls, text: str) -> str:
        return re.sub(pattern=r"\[\^\d+\^\]\s?", repl="", string=text)

    @classmethod
    def escape_markdown_v2(cls, text: str) -> str:
        return re.sub(pattern=cls.esc_pattern, repl=r"\\\1", string=text)
