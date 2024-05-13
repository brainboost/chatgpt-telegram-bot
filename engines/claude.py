import json
import logging
import os
import time
import uuid
from typing import Any

import boto3
import boto3.session
from curl_cffi import requests
from requests_toolbelt import MultipartEncoder

from .common_utils import (
    encode_message,
    escape_markdown_v2,
    get_s3_file,
    read_json_from_s3,
    read_ssm_param,
)
from .mime_types import mime_types
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")
engine_type = "claude"
request_timeout = 600
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


def get_content_type(file_path) -> str:
    extension = os.path.splitext(file_path)[-1].lower()
    return mime_types.get(extension, "application/octet-stream")


def upload_attachment(tmp_file: str, content_type: str) -> Any:
    file_name = os.path.basename(tmp_file)
    m = MultipartEncoder(
        fields={
            "file": (file_name, open(tmp_file, "rb"), content_type),
            "orgUuid": (None, organization_id),
        }
    )
    url = f"{base_url}/api/{organization_id}/upload"
    req_headers = dict(headers)
    req_headers["Content-Type"] = m.content_type
    response = requests.post(
        url,
        headers=req_headers,
        impersonate=browser_version,
        data=m.to_string(),
        timeout=request_timeout,
    )
    logging.info(f"Uploaded file {tmp_file}, response '{response.status_code}'")
    # os.remove(tmp_file)
    if response.status_code == 200:
        return response.json()
    else:
        logging.error(f"POST upload returned {response.status_code} {response.reason}")
        logging.info(response.content.decode("utf-8"))
        logging.info(headers)
        return None


def convert_attachment(tmp_file: str, content_type: str) -> Any:
    file_name = os.path.basename(tmp_file)
    m = MultipartEncoder(
        fields={
            "file": (file_name, open(tmp_file, "rb"), content_type),
            "orgUuid": (None, organization_id),
        }
    )
    url = f"{base_url}/api/convert_document"
    req_headers = dict(headers)
    req_headers["Content-Type"] = m.content_type
    response = requests.post(
        url,
        headers=req_headers,
        impersonate=browser_version,
        data=m.to_string(),
        timeout=request_timeout,
    )
    logging.info(f"Uploaded file {tmp_file}, response '{response.status_code}'")
    # os.remove(tmp_file)
    if response.status_code == 200:
        return response.json()
    else:
        logging.error(f"POST upload returned {response.status_code} {response.reason}")
        logging.info(response.content.decode("utf-8"))
        logging.info(headers)
        return None


def ask(context: UserContext, text: str, attachments=None, files=None):
    if "/ping" in text:
        return "pong"

    conversation_uuid = context.conversation_id or __generate_uuid()
    __set_conversation(conversation_id=conversation_uuid)
    context.conversation_id = conversation_uuid
    __set_title(prompt=text, conversation_id=conversation_uuid)
    payload = {
        "prompt": text,
        "timezone": "Europe/Warsaw",
        "attachments": attachments or [],
        "files": files or [],
    }
    post_response = requests.post(
        f"{base_url}/api/organizations/{organization_id}/chat_conversations/{conversation_uuid}/completion",
        headers=headers,
        data=json.dumps(payload),
        impersonate=browser_version,
        timeout=request_timeout,
    )
    if not post_response.ok:
        logging.error(
            f"POST request returned {post_response.status_code} {post_response.reason}"
        )
        logging.info(post_response.content.decode("utf-8"))
        logging.info(payload)

    time.sleep(2)
    response = requests.get(
        f"{base_url}/api/organizations/{organization_id}/chat_conversations/{conversation_uuid}",
        headers=headers,
        impersonate=browser_version,
        timeout=request_timeout,
    )
    if not response.ok:
        logging.error(
            f"GET request for conversation {conversation_uuid} returned {response.status_code} {response.reason}"
        )
        return response.text
    decoded_data = response.content.decode("utf-8")
    # logging.info(decoded_data)
    data = json.loads(decoded_data)
    last = data["chat_messages"][-1]
    return escape_markdown_v2(last["text"])


def process_attachments(attachments: str) -> tuple:
    attachment_response = []
    other_files = []
    if attachments:
        logging.info("Uploading attachments")
        logging.info(attachments)
        tmp_file_name = get_s3_file(attachments, bucket_name)
        logging.info(f"Uploads saved to {tmp_file_name}")
        content_type = get_content_type(tmp_file_name)
        if "image/" in content_type:
            logging.info(f"Uploading attachment of mimetype {content_type}")
            upload_response = upload_attachment(
                tmp_file=tmp_file_name, content_type=content_type
            )
            if upload_response:
                logging.info(f"Upload response: {upload_response}")
                other_files.append(upload_response["file_uuid"])
            else:
                logging.error("File uploads failed")
        elif "text/" in content_type:
            file_size = os.path.getsize(tmp_file_name)
            logging.info(
                f"Reading text content of mimetype {content_type} of size {file_size}"
            )
            with open(tmp_file_name, "r", encoding="utf-8") as file:
                file_content = file.read()
            attachment_response.append(
                {
                    "file_name": tmp_file_name,
                    "file_type": "text/plain",
                    "file_size": file_size,
                    "extracted_content": file_content,
                }
            )
        else:
            logging.info(f"Converting attachment of mimetype {content_type}")
            uploaded = convert_attachment(
                tmp_file=tmp_file_name, content_type=content_type
            )
            if uploaded:
                attachment_response.append(uploaded)
            else:
                logging.error("Converting attachment failed")
    return (attachment_response, other_files)


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
    headers["Referer"] = f"{base_url}/chat/{conversation_id}"


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
        "message_content": prompt,
        "recent_titles": [],
    }
    response = requests.post(
        url=f"{base_url}/api/organizations/{organization_id}/chat_conversations/{conversation_id}/title",
        headers=headers,
        data=json.dumps(payload),
        impersonate=browser_version,
    )
    if not response.ok:
        logging.error(response.text)
        return "Untitled"
    title = response.json()["title"]
    logging.info(f"Title set to '{title}")
    return title


bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
cookies = read_json_from_s3(bucket_name, "claude-cookies.json")
logging.info(f"Read {len(cookies)} cookies from s3")
cookies_str = ""
for cookie_data in cookies:
    cookies_str += f"{cookie_data['name']}={cookie_data['value']};"
headers["Cookie"] = cookies_str
organization_id = __get_organization()

result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")


def process_payload(payload: Any, request_id: str) -> None:
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
    attachments_tuple = process_attachments(attachments=payload.get("file", None))
    response = ask(
        context=user_context,
        text=payload["text"],
        attachments=attachments_tuple[0],
        files=attachments_tuple[1],
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
        process_payload(payload, request_id)
