import json
import logging
import re
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
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")
engine_type = "claude"


base_url = "https://claude.ai"
browser_version = "chrome110"
headers = {
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0",  # noqa: E501
}
bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
cookies = read_json_from_s3(bucket_name, "claude-cookies.json")
logging.info(f"Read {len(cookies)} cookies from s3")
cookies_str = ""
for cookie_data in cookies:
    cookies_str += f"{cookie_data['name']}={cookie_data['value']};"
headers["Cookie"] = cookies_str
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")
history = ConversationHistory()


def __get_organization():
    url = f"{base_url}/api/organizations"
    response = requests.get(url, headers=headers, impersonate=browser_version)
    if not response.ok:
        logging.error(f"Cannot get organizationID. {response.reason}")
        logging.info(headers)
    res = json.loads(response.text)
    uuid = res[0]["uuid"]
    logging.info(f"Got organisationID {uuid}")
    return uuid


organization_id = __get_organization()
user_context = UserContext()


def set_conversation(conversation_id: str) -> None:
    logging.info(f"conversation_id: {conversation_id}")
    url = f"{base_url}/api/organizations/{organization_id}/chat_conversations"
    payload = json.dumps({"uuid": conversation_id, "name": ""})
    response = requests.post(
        url, headers=headers, data=payload, impersonate=browser_version
    )
    if not response.ok:
        logging.info(f"http status {response.status_code}")
        e = f"Cannot create a chat. Request returned {response.status_code}"
        logging.error(e)
        raise Exception(e)
    logging.info(f"Claude conversation has been reset. New ID {conversation_id}")


def __generate_uuid() -> str:
    random_uuid = uuid.uuid4()
    random_uuid_str = str(random_uuid)
    formatted_uuid = f"{random_uuid_str[0:8]}-{random_uuid_str[9:13]}-{random_uuid_str[14:18]}-{random_uuid_str[19:23]}-{random_uuid_str[24:]}"  # noqa: E501
    return formatted_uuid


def set_title(prompt: str, conversation_id: str) -> str:
    payload = {
        "organization_uuid": organization_id,
        "conversation_uuid": conversation_id,
        "message_content": prompt,
        "recent_titles": [],
    }
    response = requests.post(
        url=f"{base_url}/api/generate_chat_title",
        headers=headers,
        data=json.dumps(payload),
        impersonate=browser_version,
    )
    if not response.ok:
        logging.error(response.text)
        raise Exception(f"Error response {response.text}")
    title = response.json()["title"]
    return title


def ask(prompt: str, conversation_id: str, attachment=None):
    if "/ping" in prompt:
        return "pong"

    conversation_uuid = conversation_id or __generate_uuid()
    set_conversation(conversation_id=conversation_uuid)
    set_title(prompt=prompt, conversation_id=conversation_uuid)
    conversation_id = conversation_uuid
    attachments = []
    # if attachment:
    #     attachment_response = upload_attachment(attachment)
    #     if attachment_response:
    #         attachments = [attachment_response]
    #     else:
    #         return {"File upload failed. Please try again."}
    if not attachment:
        attachments = []
    payload = json.dumps(
        {
            "completion": {
                "prompt": f"{prompt}",
                "timezone": "Europe/Warsaw",
                "model": "claude-2",
            },
            "organization_uuid": organization_id,
            "conversation_uuid": conversation_uuid,
            "text": f"{prompt}",
            "attachments": attachments,
        }
    )
    response = requests.post(
        f"{base_url}/api/append_message",
        headers=headers,
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
        data = json.loads(json_str)
        if "completion" in data:
            completions.append(data["completion"])
    answer = "".join(completions)
    return escape_markdown_v2(answer)


def sqs_handler(event, context):
    """AWS SQS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["body"])
        user_id = payload["user_id"]
        user_chat_id = f"{user_id}_{payload['chat_id']}"
        conversation_id = user_context.read(user_chat_id, engine_type) or None
        logging.info(f"Read conversation_id {conversation_id}")
        response = ask(payload["text"], conversation_id=conversation_id)
        user_context.write(
            user_chat_id=user_chat_id,
            engine=engine_type,
            conversation_id=str(conversation_id),
        )
        try:
            history.write(
                conversation_id=conversation_id,
                request_id=request_id,
                user_id=user_id,
                conversation=response,
            )
        except Exception as e:
            logging.error(
                f"History write error, conversation_id: {conversation_id}, request_id: {request_id}",
                exc_info=e,
            )
        payload["response"] = encode_message(response)
        payload["engine"] = engine_type
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))


# if __name__ == "__main__":
#     put_request("does DALL-E uses stable diffusion?")
