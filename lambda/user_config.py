import json
import logging
import time

import boto3
from engines import Engines

logging.basicConfig()
logging.getLogger().setLevel("INFO")


class UserConfig:
    def __init__(self) -> None:
      dynamodb = boto3.resource("dynamodb")
      self.table = dynamodb.Table("user-configurations")

    def read(self, user_id: int):
      try:
        resp = self.table.get_item(Key = { "user_id" : user_id })
        if "Item" in resp:
          return json.loads(resp["Item"]["config"])
        return self.create_config(user_id)

      except Exception as e:
        logging.error(e)

    def write(self, user_id: int, config):  
      self.table.put_item(Item={"user_id": user_id, "config": json.dumps(config)})

    def create_config(self, user_id: int):
      return {
        "user_id": user_id,
        "config": {
          "plaintext": False,
          "engine": Engines.BING,
          "updated": int(time.time()),
        }
      }