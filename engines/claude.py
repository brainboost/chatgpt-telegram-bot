import json
import logging
import os
import re
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

import boto3
import boto3.session
import requests as req
from curl_cffi import requests

from .common_utils import (
    encode_message,
    escape_markdown_v2,
    read_json_from_s3,
    read_ssm_param,
)
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


def process_command(input: str, context: UserContext) -> None:
    command = input.removeprefix(prefix="/").lower()
    logging.info(f"Processing command {command} for {context.user_id}")
    if "reset" in command:
        context.reset_conversation()
        logging.info(f"Conversation hass been reset for {context.user_id}")
        return
    logging.error(f"Unknown command {command}")


def get_content_type(file_path):
    extension = os.path.splitext(file_path)[-1].lower()
    if extension == ".pdf":
        return "application/pdf"
    elif extension == ".txt":
        return "text/plain"
    elif extension == ".csv":
        return "text/csv"
    else:
        return "application/octet-stream"


def upload_attachment(s3_uri: str) -> Optional[str]:
    url = "https://claude.ai/api/convert_document"
    parsed = urlparse(s3_uri)
    s3_bucket, s3_path, file_name = (
        parsed.netloc,
        parsed.path,
        parsed.path.split("/")[-1],
    )
    logging.info(f"Downloading file {file_name} from s3 bucket {s3_bucket}")
    tmp_file = f"/tmp/{file_name}"
    # tmp_file = "/tmp/Regulamin_promocji.pdf"
    session = boto3.session.Session()
    session.client("s3").download_file(Bucket=s3_bucket, Key=s3_path, Filename=tmp_file)
    # session.client("s3").download_file(
    #     Bucket="chatbotstack-s3-bucket-dev-tmp",
    #     Key="att/Regulamin_promocji.pdf",
    #     Filename=tmp_file,
    # )
    files = {
        "file": (file_name, open(tmp_file, "rb"), get_content_type(tmp_file)),
        "orgUuid": (None, organization_id),
    }
    response = req.post(url, headers=headers, files=files)
    logging.info(f"Uploaded file {file_name}, response '{response.status_code}'")
    os.remove(tmp_file)
    if response.status_code == 200:
        return response.json()
    else:
        logging.error(f"POST upload returned {response.status_code} {response.reason}")
        logging.info(response.content.decode("utf-8"))
        logging.info(headers)
        logging.info(files)
        return None


def ask(text: str, context: UserContext, attachment=None):
    if "/ping" in text:
        return "pong"

    conversation_uuid = context.conversation_id or __generate_uuid()
    __set_conversation(conversation_id=conversation_uuid)
    context.conversation_id = conversation_uuid
    __set_title(prompt=text, conversation_id=conversation_uuid)
    # attachment_response = upload_attachment(attachment)
    # logging.info(attachment_response)
    # attachments = [attachment_response]
    # if attachment:
    #     logging.info(f"Uploading attachment {attachment}")
    #     attachment_response = upload_attachment(attachment)
    #     if attachment_response:
    #         attachments = [attachment_response]
    #     else:
    #         logging.error("File upload failed: {}".format(attachment))
    attachments = []
    payload = json.dumps(
        {
            "completion": {
                "prompt": f"{text}",
                "timezone": "Europe/Warsaw",
                "model": "claude-2.1",
            },
            "organization_uuid": organization_id,
            "conversation_uuid": conversation_uuid,
            "text": f"{text}",
            "attachments": attachments,
        }
    )
    response = requests.post(
        f"{base_url}/api/append_message",
        headers=headers,
        data=payload,
        impersonate=browser_version,
        timeout=600,
    )
    if not response.ok:
        logging.error(f"POST request returned {response.status_code} {response.reason}")
        logging.info(response.content.decode("utf-8"))
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


def __set_conversation(conversation_id: str) -> None:
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
    logging.info(f"Generated new uuid '{formatted_uuid}'")
    return formatted_uuid


def __get_organization():
    url = f"{base_url}/api/organizations"
    response = requests.get(url, headers=headers, impersonate=browser_version)
    if not response.ok:
        logging.error(
            f"Cannot get organizationID.{response.status_code} {response.reason} {response.text}"
        )
        logging.info(headers)
    res = json.loads(response.text)
    uuid = res[0]["uuid"]
    logging.info(f"Got organisationID '{uuid}'")
    return uuid


def __set_title(prompt: str, conversation_id: str) -> str:
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
    logging.info(f"Title set to '{title}")
    return title


cookies = read_json_from_s3(
    read_ssm_param(param_name="BOT_S3_BUCKET"), "claude-cookies.json"
)
logging.info(f"Read {len(cookies)} cookies from s3")
cookies_str = ""
for cookie_data in cookies:
    cookies_str += f"{cookie_data['name']}={cookie_data['value']};"
headers["Cookie"] = cookies_str
organization_id = __get_organization()

result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")


def __process_payload(payload: Any, request_id: str) -> None:
    user_id = payload["user_id"]
    user_context = UserContext(
        user_id=f"{user_id}_{payload['chat_id']}",
        request_id=request_id,
        engine_id=engine_type,
        username=payload["username"],
    )
    if "command" in payload["type"]:
        process_command(input=payload["text"], context=user_context)
        return
    response = ask(
        text=payload["text"], context=user_context, attachment=payload.get("file", None)
    )
    user_context.save_conversation(
        conversation={"request": payload["text"], "response": response},
    )
    payload["response"] = encode_message(response)
    payload["engine"] = engine_type
    sns.publish(TopicArn=result_topic, Message=json.dumps(payload))


def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        __process_payload(payload, request_id)


# if __name__ == "__main__":
#     ask("a ile kosztuje ta usługa miesięczne?")
