import asyncio
import json

import boto3
from engines import EngineInterface, Engines


class BingGptMock(EngineInterface):
    def __init__(self) -> None:
        _ssm_client = boto3.client(service_name="ssm")
        self.s3_bucket = _ssm_client.get_parameter(Name="CHATBOT_S3_BUCKET")[
          "Parameter"
          ]["Value"]
    
    def reset_chat(self) -> None:
      pass
    
    async def ask(self, text, userConfig: dict) -> str:
        return await asyncio.run(self.read_mock_data(self.s3_bucket))
    
    def close(self):
        pass

    @property
    def engine_type(self):
        return Engines.BING

    def read_mock_data(self, bucket_name:str) -> dict:
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=bucket_name, Key="bing_gpt_mock_data.json")
        file_content = response["Body"].read().decode("utf-8")
        return json.loads(file_content)
