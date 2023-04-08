import asyncio
import json
import logging
import time
from typing import Optional

import boto3

logging.basicConfig()
logging.getLogger().setLevel("INFO")


class ConversationHistory:
    def __init__(self) -> None:
      dynamodb = boto3.resource("dynamodb")
      self.table = dynamodb.Table("conversations")

    def read(self, conversation_id: str, request_id: str) -> Optional[dict]:
      try:
        resp = self.table.get_item(Key = { 
            "conversation_id" : conversation_id,
            "request_id": request_id 
          })
        if "Item" in resp:
          return json.loads(resp["Item"]["conversation"])
        return None
      except Exception as e:
        logging.error(e)

    async def write_async(self, conversation_id: str, request_id: str, user_id: int, 
        conversation):  
        asyncio.run(self.table.put_item(
          Item={
            "conversation_id": conversation_id, 
            "request_id": request_id,
            "user_id": user_id,
            "timestamp": int(time.time()),
            "conversation": json.dumps(conversation)
          })
        )      
