import asyncio
import logging
import uuid

import boto3
from conversation import Conversation
from engines import EngineInterface, Engines

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

    async def ask(self, text) -> str:
        try:
            response = await asyncio.run(self.chatsonic.send_message(message=text))

        except Exception as e:
            logging.error(e)
            message = f"{e}"
        else:
            logging.info(response)
            message = next(
                (r["message"] for r in response if r["is_sent"] is False), ""
            )
        return message
    
    def close(self):
        pass

    @property
    def engine_type(self):
        return Engines.CHATSONIC
