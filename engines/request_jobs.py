import datetime
import logging
from typing import Any, Optional

import boto3


class RequestJobs:
    def __init__(
        self,
        request_id: str,
        engine_id: str,
    ):
        self.engine_id = engine_id
        self.request_id = request_id
        dynamodb = boto3.resource("dynamodb")
        self.requests_table = dynamodb.Table("request-jobs")

    def read(self) -> Optional[dict]:
        logging.info(f"Read request context '{self.request_id}'")
        try:
            resp = self.requests_table.get_item(
                Key={"request_id": self.request_id, "engine": self.engine_id}
            )
            if "Item" in resp and resp["Item"]:
                return resp["Item"]
        except Exception as e:
            logging.error(
                f"Cannot read from 'request-jobs' table with PK '{self.request_id}' and SK '{self.engine_id}'",
                exc_info=e,
            )
        return None

    def save(self, context: Optional[Any]) -> None:
        logging.info(
            f"Save request context for {self.request_id}, engine {self.engine_id}"
        )
        tme = datetime.datetime.utcnow()
        exp_time = tme + datetime.timedelta(days=10)
        self.requests_table.put_item(
            Item={
                "request_id": self.user_id,
                "engine": self.engine_id,
                "context": context or None,
                "timestamp": int(tme.timestamp()),
                "exp": int(exp_time.timestamp()),
            }
        )

    def delete(self) -> None:
        logging.info(
            f"Delete request context {self.request_id}, engine {self.engine_id}"
        )
        self.requests_table.delete_item(
            Key={
                "request_id": {
                    "S": self.request_id,
                },
                "engine": {"S": self.engine_id},
            },
            Table="conversation-id-index",
        )
        self.conversation_id = None
