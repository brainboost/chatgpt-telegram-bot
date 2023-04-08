import json
import logging
import re
import uuid

import boto3
import utils
from conversation_history import ConversationHistory
from EdgeGPT import Chatbot
from engine_interface import EngineInterface

logging.basicConfig()
logging.getLogger().setLevel("INFO")

class BingGpt(EngineInterface):
    
    def __init__(self, chatbot: Chatbot) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatbot = chatbot
        self.history = ConversationHistory()

    def reset_chat(self) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatbot.reset()

    async def ask_async(self, text: str, userConfig: dict) -> str:
        style = userConfig.get("style", "balanced")
        response = await self.chatbot.ask(prompt=text, 
            conversation_style=style)
        item = response["item"]
        self.conversation_id = item["conversationId"]
        try:
            await self.history.write_async( 
                conversation_id=self.conversation_id, 
                request_id=item["requestId"],
                user_id=userConfig["user_id"], 
                conversation=item)
        except Exception as e:
            logging.error(f"conversation_id: {self.conversation_id}, error: {e}")
        finally:
            logging.info(json.dumps(response, default=vars))
        
        if "plaintext" in userConfig is True:
            return utils.read_plain_text(response)
        return BingGpt.read_markdown(response)
    
    async def close(self):
        await self.chatbot.close()

    @property
    def engine_type(self):
        return "bing"

    def read_cookies(self, s3_path) -> dict:
        s3 = boto3.client("s3")
        bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
        response = s3.get_object(Bucket=bucket_name, Key=file_name)
        file_content = response["Body"].read().decode("utf-8")
        return json.loads(file_content)

    @classmethod
    def create(cls) -> EngineInterface:
        s3_path = utils.read_ssm_param(param_name="COOKIES_FILE")
        bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
        chatbot = Chatbot(cookies=utils.read_json_from_s3(bucket_name, file_name))
        bing = BingGpt(chatbot)
        return bing

    @classmethod
    def read_plain_text(cls, response: dict) -> str:
        return re.sub(pattern=cls.remove_links_pattern, repl="", 
            string=response["item"]["messages"][1]["text"])

    @classmethod
    def read_markdown(cls, response: dict) -> str:
        message = response["item"]["messages"][1]["adaptiveCards"][0]["body"][0]["text"]
        return utils.replace_references(text=message)

