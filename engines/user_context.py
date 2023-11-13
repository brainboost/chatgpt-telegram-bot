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
        engine_id: str,
        request_id: str,
        username: Optional[str],
    ) -> None:
        self.user_id = user_id
        self.username = username or "anonymous"
        self.engine_id = engine_id
        self.request_id = request_id
        dynamodb = boto3.resource("dynamodb")
        self.context_table = dynamodb.Table("user-context")
        self.conversations_table = dynamodb.Table("user-conversations")
        self.context = self.read_context()
        self.conversation_id = self.__get_conversation_id()
        self.parent_id = self.__get_parent_id()

    def reset_conversation(self) -> None:
        logging.info(f"Reset conversation {self.conversation_id}")
        self.context_table.delete_item(
            Key={
                "conversation_id": {
                    "S": self.conversation_id,
                }
            },
            Table="conversation-id-index",
        )
        self.conversation_id = None

    def read_context(self) -> Optional[Any]:
        logging.info(f"Read user context {self.user_id}")
        try:
            resp = self.context_table.get_item(
                Key={"user_id": self.user_id, "engine": self.engine_id}
            )
            if "Item" in resp and resp["Item"]:
                item = resp["Item"]
                return {
                    "user_id": item["user_id"],
                    "engine": item["engine"],
                    "conversation_id": item["conversation_id"],
                    "parent_id": item["parent_id"],
                    "optional": json.loads(item["optional"] or {}),
                    "exp": int(item["exp"]),
                }
        except Exception as e:
            logging.error(
                f"Cannot read from 'user-context' table with PK '{self.user_id}' and SK '{self.engine_id}'",
                exc_info=e,
            )
        return None

    def save_context(self, optional_context: Optional[Any] = None) -> None:
        logging.info(f"Save user context for {self.user_id}, engine {self.engine_id}")
        tme = datetime.datetime.utcnow()
        exp_time = tme + datetime.timedelta(days=60)
        self.context_table.put_item(
            Item={
                "user_id": self.user_id,
                "engine": self.engine_id,
                "conversation_id": self.conversation_id,
                "parent_id": self.parent_id,
                "optional": json.dumps(optional_context or {}),
                "exp": int(exp_time.timestamp()),
            }
        )

    def save_conversation(
        self,
        conversation: Optional[Any] = None,
    ) -> None:
        logging.info(f"Save conversation for {self.user_id}, engine {self.engine_id}")
        tme = datetime.datetime.utcnow()
        try:
            self.save_context(optional_context=None)
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
                f"save_conversation failed with error. User: {self.user_id}, engine_id: {self.engine_id}, conversation_id: {self.conversation_id}",
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

    def __get_conversation_id(self) -> Optional[str]:
        if self.context:
            self.context.get("conversation_id", None)
        return None

    def __get_parent_id(self) -> Optional[str]:
        if self.context:
            self.context.get("parent_id", None)
        return None
