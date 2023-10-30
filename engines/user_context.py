import datetime
import json
import logging
from typing import Optional

import boto3

logging.basicConfig()
logging.getLogger().setLevel("INFO")


class UserContext:
    def __init__(self) -> None:
        dynamodb = boto3.resource("dynamodb")
        self.table = dynamodb.Table("user-context")

    def read(self, user_chat_id: str, engine: str) -> Optional[str]:
        try:
            resp = self.table.get_item(
                Key={"user_chat": user_chat_id, "engine": engine}
            )
            if "Item" in resp and resp["Item"]["conversation_id"]:
                return json.loads(resp["Item"]["conversation_id"])
        except Exception:
            logging.error(
                f"Cannot read from 'user-context' table with PK {user_chat_id} and SK {engine}"
            )
        return None

    def write(self, user_chat_id: str, engine: str, conversation_id: str):
        exp_time = datetime.datetime.utcnow() + datetime.timedelta(days=30)
        self.table.put_item(
            Item={
                "user_chat": user_chat_id,
                "engine": engine,
                "conversation_id": conversation_id,
                "exp": int(exp_time.timestamp()),
            }
        )
