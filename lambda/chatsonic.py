import json
import logging
import re
import uuid

import boto3
import utils
from conversation import Conversation
from engine_interface import EngineInterface

logging.getLogger().setLevel("INFO")


class ChatSonic(EngineInterface):
    def __init__(self) -> None:
        _ssm_client = boto3.client(service_name="ssm")
        token = _ssm_client.get_parameter(Name="CHATSONIC_TOKEN")["Parameter"]["Value"]
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatsonic = Conversation(api_key=token, enable_memory=True)

    def reset_chat(self) -> None:
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())

    async def ask_async(self, text: str, userConfig: dict) -> str:
        try:
            response = self.chatsonic.send_message(message=text)
            logging.info(json.dumps(response, default=vars))

        except Exception as e:
            logging.error(e)
            message = f"{e}"
        else:
            logging.info(response)
            message = next(
                (r["message"] for r in response if r["is_sent"] is False), ""
            )
            if "plaintext" in userConfig is True:
                return ChatSonic.read_plain_text(message)
            return ChatSonic.read_markdown(message)
        return message
    
    def close(self):
        pass

    @property
    def engine_type(self):
        return "chatsonic"
    
    @classmethod
    def read_plain_text(cls, response: dict) -> str:
        return re.sub(pattern=cls.remove_links_pattern, repl="", 
            string=response["item"]["messages"][1]["text"])

    @classmethod
    def read_markdown(cls, response: dict) -> str:
        message = response["item"]["messages"][1]["adaptiveCards"][0]["body"][0]["text"]
        return utils.escape_markdown_v2(text=message)
