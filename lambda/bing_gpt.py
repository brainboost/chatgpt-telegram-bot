import json
import logging
import re
import uuid

import utils
from EdgeGPT import Chatbot
from engines import EngineInterface, Engines
from telegram import constants, helpers

logging.basicConfig()
logging.getLogger().setLevel("INFO")


class BingGpt(EngineInterface):
    def __init__(self, chatbot: Chatbot) -> None:
        # _ssm_client = boto3.client(service_name="ssm")
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatbot = chatbot

    def reset_chat(self) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())

    async def ask(self, text: str, userConfig: dict) -> str:
        response = await self.chatbot.ask(prompt=text)
        logging.info(json.dumps(response, default=vars))
        if "plaintext" in userConfig is True:
            logging.info("return results in plaintext")
            return BingGpt.read_plain_text(response)
        return BingGpt.read_markdown(response)
    
    @classmethod
    def ask_stub(self, text: str, userConfig: dict) -> str:
        response = utils.read_json_from_s3(utils.read_param(param_name="CHATBOT_S3_BUCKET"), 
            "bing_gpt_mock_data.json")
        # logging.info(json.dumps(response, default=vars))
        if "plaintext" in userConfig is True:
            logging.info("return results in plaintext")
            return BingGpt.read_plain_text(response)
        return BingGpt.read_markdown(response)

    async def close(self):
        await self.chatbot.close()

    @property
    def engine_type(self):
        return Engines.BING

    @classmethod
    def create() -> EngineInterface:
        s3_path = utils.read_param(param_name="COOKIES_FILE")
        bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
        chatbot = Chatbot(cookies=utils.read_json_from_s3(bucket_name, file_name))
        return BingGpt(chatbot)


    @classmethod
    def read_plain_text(response: dict) -> str:
        return response["item"]["messages"][1]["text"]

    @classmethod
    def read_markdown(response: dict) -> str:
        message = response["item"]["messages"][1]["adaptiveCards"][0]["body"][0]["text"]
        markdown = BingGpt.replace_references(message)
        return helpers.escape_markdown(markdown, 2, constants.ParseMode.MARKDOWN_V2)

    @classmethod
    def replace_references(markdown: str):
        ref_link_pattern = re.compile(r'\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?')
        ref_links = re.findall(ref_link_pattern, markdown)

        # loop through each reference link and create an inline link
        for link in ref_links:
            link_label = link[0]
            link_ref = link[1]
            inline_link = f" [[{link_label}]({link_ref})]"
            markdown = re.sub(rf'\[\^{link_label}\^\]\[{link_label}\]', 
                inline_link, markdown)
            
        # remove all reference-style links from the Markdown text
        return re.sub(ref_link_pattern, '', markdown)
