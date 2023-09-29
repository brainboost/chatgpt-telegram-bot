import json
import logging
import re
import time
import uuid

import boto3
from curl_cffi import requests

from .common_utils import (
    encode_message,
    escape_markdown_v2,
    read_json_from_s3,
    read_ssm_param,
)
from .conversation_history import ConversationHistory
from .engine_interface import EngineInterface

logging.basicConfig()
logging.getLogger().setLevel("INFO")

base_url = "https://claude.ai"
browser_version = "chrome110"


class Claude(EngineInterface):
    def __init__(self, cookies) -> None:
        cookies_str = ""
        for cookie_data in cookies:
            cookies_str += f"{cookie_data['name']}={cookie_data['value']};"
        self.headers = {
            "Cookie": f"{cookies_str}",
            "Origin": f"{base_url}",
            "Referer": f"{base_url}/chats",
            "DNT": "1",
            "Accept": "text/event-stream, text/event-stream",
            "Accept-Language": "en-US,en;q=0.5",
            "Content-Type": "application/json",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "TE": "trailers",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) \
              Gecko/20100101 Firefox/117.0",
        }
        self.parent_id = self.get_organization()
        self.history = ConversationHistory()

    def reset_chat(self) -> None:
        self.conversation_id = self.__generate_uuid()
        url = f"{base_url}/api/organizations/{self.parent_id}/chat_conversations"
        payload = json.dumps({"uuid": self.conversation_id, "name": str(time.time())})
        response = requests.post(
            url, headers=self.headers, data=payload, impersonate=browser_version
        )
        logging.info(response)
        if not response.ok:
            logging.error(f"Cannot create a new chat. {response.reason}")
        logging.info(f"Claude chat has been reset. New ID {self.conversation_id}")

    def ask(self, text: str, userConfig: dict) -> str:
        if "/ping" in text:
            return "pong"

        self.reset_chat()
        result = self.send_message(text)
        return escape_markdown_v2(result)

    def close(self):
        logging.info("called 'close' method")

    @property
    def engine_type(self):
        return "claude"

    def __generate_uuid(self) -> str:
        random_uuid = uuid.uuid4()
        random_uuid_str = str(random_uuid)
        formatted_uuid = f"{random_uuid_str[0:8]}-{random_uuid_str[9:13]}-\
          {random_uuid_str[14:18]}-{random_uuid_str[19:23]}-{random_uuid_str[24:]}"
        return formatted_uuid

    def get_organization(self):
        url = f"{base_url}/api/organizations"
        response = requests.get(url, headers=self.headers, impersonate=browser_version)
        if not response.ok:
            logging.error(f"Cannot get organizationID. {response.reason}")
            logging.info(self.headers)
        res = json.loads(response.text)
        uuid = res[0]["uuid"]
        logging.info(f"Got organisationID {uuid}")
        return uuid

    def send_message(self, prompt, attachment=None, timeout=500):
        attachments = []
        if attachment:
            attachment_response = self.upload_attachment(attachment)
            if attachment_response:
                attachments = [attachment_response]
            else:
                return {"File upload failed. Please try again."}
        if not attachment:
            attachments = []
        payload = json.dumps(
            {
                "completion": {
                    "prompt": f"{prompt}",
                    "timezone": "Europe/Warsaw",
                    "model": "claude-2",
                },
                "organization_uuid": f"{self.parent_id}",
                "conversation_uuid": f"{self.conversation_id}",
                "text": f"{prompt}",
                "attachments": attachments,
            }
        )
        response = requests.post(
            f"{base_url}/api/append_message",
            headers=self.headers,
            data=payload,
            impersonate=browser_version,
            timeout=500,
        )
        if not response.ok:
            logging.error(response)
            logging.info(payload)

        decoded_data = response.content.decode("utf-8")
        decoded_data = re.sub("\n+", "\n", decoded_data).strip()
        data_strings = decoded_data.split("\n")
        completions = []
        for data_string in data_strings:
            json_str = data_string[6:].strip()
            logging.info(json_str)
            data = json.loads(json_str)
            if "completion" in data:
                completions.append(data["completion"])
        answer = "".join(completions)
        return answer

    @classmethod
    def create(cls) -> EngineInterface:
        s3_path = read_ssm_param(param_name="COOKIES_FILE")
        bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
        cookies = read_json_from_s3(bucket_name, "claude-cookies.json")
        return Claude(cookies=cookies)


instance = Claude.create()
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")


def sqs_handler(event, context):
    """AWS SQS event handler"""
    for record in event["Records"]:
        payload = json.loads(record["body"])
        response = instance.ask(payload["text"], payload["config"])
        payload["response"] = encode_message(response)
        payload["engine"] = instance.engine_type
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))


# if __name__ == "__main__":
#     put_request("does DALL-E uses stable diffusion?")
