import datetime
import json
import logging
from typing import Any, Optional

import boto3

logging.basicConfig()
logging.getLogger().setLevel("INFO")


class UserContext:
    def __init__(
        self,
        user_id: str,
        chat_id: str,
        engine_id: str,
        request_id: str,
        username: Optional[str],
    ) -> None:
        self.user_id = f"{user_id}_{chat_id}"
        self.username = username or "anonymous"
        self.engine_id = engine_id
        self.request_id = request_id
        dynamodb = boto3.resource("dynamodb")
        self.context_table = dynamodb.Table("user-context")
        self.conversations_table = dynamodb.Table("user-conversations")
        self.conversation_id = self.get_conversation_id()

    def get_conversation_id(self) -> str:
        try:
            resp = self.context_table.get_item(
                Key={"user_id": self.user_id, "engine": self.engine_id}
            )
            if "Item" in resp and resp["Item"]["conversation_id"]:
                return resp["Item"]["conversation_id"]
        except Exception as e:
            logging.error(
                f"Cannot read from 'user-context' table with PK '{self.user_id}' and SK '{self.engine_id}'",
                exc_info=e,
            )
        return None

    def reset_conversation(self) -> None:
        self.context_table.delete_item(
            Key={
                "conversation_id": {
                    "S": self.conversation_id,
                }
            },
            Table="conversation-id-index",
        )
        self.conversation_id = None

    def save_conversation(
        self,
        conversation: Optional[Any],
    ):
        tme = datetime.datetime.utcnow()
        exp_time = tme + datetime.timedelta(days=60)
        try:
            self.context_table.put_item(
                Item={
                    "user_id": self.user_id,
                    "engine": self.engine_id,
                    "conversation_id": self.conversation_id,
                    "exp": int(exp_time.timestamp()),
                }
            )
            if self.conversation_id:
                self.conversations_table.put_item(
                    Item={
                        "conversation_id": self.conversation_id,
                        "request_id": self.request_id,
                        "user_id": self.user_id,
                        "engine": self.engine_id,
                        "timestamp": int(tme.timestamp()),
                        "conversation": json.dumps(conversation or {}),
                    }
                )
        except Exception as e:
            logging.error(
                f"save_conversation filed with error. User: {self.user_id}, engine_id: {self.engine_id}, conversation_id: {self.conversation_id}",
                exc_info=e,
            )

    def read_conversation(self) -> Optional[Any]:
        try:
            resp = self.conversations_table.get_item(
                Key={
                    "conversation_id": self.conversation_id,
                    "request_id": self.request_id,
                }
            )
            if "Item" in resp:
                return json.loads(resp["Item"]["conversation"])
            return None
        except Exception as e:
            logging.error(
                f"read_conversation filed with error. User: {self.user_id}, engine_id: {self.engine_id}, request_id: {self.request_id}, conversation_id: {self.conversation_id}",
                exc_info=e,
            )
