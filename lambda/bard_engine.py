import json
import logging
import re
import uuid

import utils
from Bard import Chatbot
from conversation_history import ConversationHistory
from engine_interface import EngineInterface

logging.basicConfig()
logging.getLogger().setLevel("INFO")

class BardEngine(EngineInterface):

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
        # style = userConfig.get("style", "balanced")
        response = self.chatbot.ask(message=text)
        # item = response["item"]
        # self.conversation_id = item["conversationId"]
        # try:
        #     await self.history.write_async( 
        #         conversation_id=self.conversation_id, 
        #         request_id=item["requestId"],
        #         user_id=userConfig["user_id"], 
        #         conversation=item)
        # except Exception as e:
        #     logging.error(f"conversation_id: {self.conversation_id}, error: {e}")
        # finally:
        logging.info(json.dumps(response, default=vars))
        return response["message"]
        # if "plaintext" in userConfig is True:
        #     return utils.read_plain_text(response)
        # return BardEngine.read_markdown(response)
    
    async def close(self):
        await self.chatbot.close()

    @property
    def engine_type(self):
        return "bard"

    @classmethod
    def create(cls) -> EngineInterface:
        token = utils.read_ssm_param(param_name="BARD_TOKEN")
        chatbot = Chatbot(session_id=token)
        return BardEngine(chatbot)

    @classmethod
    def read_plain_text(cls, response: dict) -> str:
        return re.sub(pattern=cls.remove_links_pattern, repl="", 
            string=response["item"]["messages"][1]["text"])

    @classmethod
    def read_markdown(cls, response: dict) -> str:
        message = response["item"]["messages"][1]["adaptiveCards"][0]["body"][0]["text"]
        return utils.replace_references(text=message)
