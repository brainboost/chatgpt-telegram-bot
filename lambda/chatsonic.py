import uuid
import logging
import boto3
from  Conversation import Conversation

logging.getLogger().setLevel('INFO')

class ChatSonic:
    def __init__(self) -> None:
        _ssm_client = boto3.client(service_name='ssm')
        token = _ssm_client.get_parameter(Name="CHATSONIC_TOKEN")["Parameter"]["Value"]
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())
        self.chatsonic = Conversation(api_key=token, enable_memory=True)

    def reset_chat(self):
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())

    def ask(self, text):
        try:
          response = self.chatsonic.send_message(message=text)

        except Exception as e:
            logging.error(e)
        else:
            logging.info(response)
            message = response
        return message