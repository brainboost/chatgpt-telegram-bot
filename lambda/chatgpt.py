import uuid
import boto3
import json
import requests
import time
import logging

logging.getLogger().setLevel("INFO")


class ChatGPT:
    def __init__(self) -> None:
        ssm = boto3.client(service_name="ssm")
        self.token = ssm.get_parameter(Name="CHATGPT_TOKEN")["Parameter"]["Value"]
        self.url = ssm.get_parameter(Name="CHATGPT_URL")["Parameter"]["Value"]
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())

    def reset_chat(self):
        self.conversation_id = None
        self.parent_id = str(uuid.uuid4())

    def _get_headers(self):
        return {
            "Authorization": f"{self.token}",
            "Content-Type": "application/json",
        }

    def ask(self, text) -> str:
        headers = self._get_headers()
        data = {
            "content": text,
        }
        if self.conversation_id is not None:
            data["conversation_id"] = self.conversation_id
            data["parent_id"] = self.parent_id
        try:
            response = self.retry_post(
                f"{self.url}api/ask", headers=headers, data=json.dumps(data)
            )
        except Exception as e:
            logging.error(e)
            raise e

        if response is None:
            message = ""
        elif response.status_code == 401:
            message = "You are not authorized to access this service"
            logging.error("unauthorized access")
        elif response.status_code >= 500:
            message = "..."
            logging.error(f"Request:{data}, Response:{response}")
        else:
            # logging.info(response.text)
            result = response.json()
            self.parent_id = result["id"]
            self.conversation_id = result["conversation_id"]
            message = result["content"]

        return message

    def retry_post(self, url, headers, data, retries=3, backoff_factor=0.9):
        for i in range(retries):
            try:
                response = requests.post(
                    url=url, headers=headers, timeout=120.0, data=data
                )
                response.raise_for_status()
                return response
            except requests.exceptions.HTTPError as error:
                if i == retries - 1:
                    raise error
                backoff_time = backoff_factor * (2**i)
                logging.error(f"HTTP {error}. Retrying in {backoff_time} sec")
                time.sleep(backoff_time)
