import json
import logging
import re
from typing import Optional

import boto3
import requests
from bardapi import Bard

from .common_utils import encode_message, read_json_from_s3, read_ssm_param, save_to_s3
from .conversation_history import ConversationHistory
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "bard"
headers = {
    "Host": "bard.google.com",
    "X-Same-Domain": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; "
    "rv:109.0) Gecko/20100101 Firefox/118.0",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "Origin": "https://bard.google.com",
    "Referer": "https://bard.google.com/",
}
cookie_names = [
    "__Secure-1PSID",
    "__Secure-1PSIDTS",
    "__Secure-1PSIDCC",
    "__Secure-1PAPISID",
]
bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")
user_context = UserContext()
history = ConversationHistory()


def ask(chatbot: Bard, request_id: str, text: str, userConfig: dict) -> str:
    if "/ping" in text:
        return "pong"

    try:
        response = chatbot.get_answer(input_text=text)
    except Exception as e:
        logging.error("get_answer request failed, check cookies", exc_info=e)
        return "Sorry, requesting the Bard AI model failed"

    if not response or len(response) < 1 or "content" not in response:
        logging.error(response)
        raise Exception("Wrong Bard response, check logs")

    if chatbot.session and chatbot.session.cookies:
        logging.info("Saving cookies")
        cookies = []
        try:
            for name in cookie_names:
                cookie_value = chatbot.session.cookies.get(name=name)
                cookies.append(
                    {
                        "name": name,
                        "value": cookie_value,
                        "path": "/",
                        "domain": ".google.com",
                    }
                )
            save_to_s3(bucket_name, "bard-cookies.json", cookies)
            logging.info(f"Saved {len(cookies)} cookies to s3")
        except Exception as e:
            logging.error("Error saving session cookies", exc_info=e)

    item = response["content"]
    answer = __as_markdown(item)
    return answer


def create(conversation_id: Optional[str]) -> Bard:
    socks_url = read_ssm_param(param_name="SOCKS5_URL")
    proxies = {"https": socks_url, "http": socks_url}
    auth_cookies = read_json_from_s3(bucket_name, "bard-cookies.json")
    logging.info(f"Read {len(auth_cookies)} cookies from s3")
    psid = [x.get("value") for x in auth_cookies if x.get("name") == "__Secure-1PSID"][
        0
    ]
    session = requests.Session()
    session.headers = headers
    for i in range(len(auth_cookies)):
        if auth_cookies[i]["name"] in cookie_names:
            session.cookies.set(
                name=auth_cookies[i]["name"],
                value=auth_cookies[i]["value"],
                domain=auth_cookies[i]["domain"],
                path=auth_cookies[i]["path"],
            )
    logging.info(
        f"psid {psid}, proxy: {socks_url}, cookies: {len(session.cookies.items())}"
    )
    return Bard(
        token=psid,
        proxies=proxies,
        conversation_id=conversation_id,
        session=session,
        timeout=40,
    )


def __as_markdown(input: str) -> str:
    input = re.sub(r"(?<!\*)\*(?!\*)", "\\\\*", input)
    input = re.sub(r"\*{2,}", "*", input)
    esc_pattern = re.compile(f"([{re.escape(r'._-+#|{}!=()<>[]')}])")
    return re.sub(esc_pattern, r"\\\1", input)


def process_command(input: str) -> None:
    command = input.removeprefix(prefix="/").lower()
    if "reset" in command:
        # do reset
        return
    logging.error(f"Unknown command {command}")


def sqs_handler(event, context):
    """AWS SQS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["body"])
        logging.info(payload)
        user_id = payload["user_id"]
        user_chat_id = f"{user_id}_{payload['chat_id']}"
        conversation_id = user_context.read(user_chat_id, engine_type) or None
        logging.info(f"Read conversation_id {conversation_id}")
        instance = create(conversation_id=conversation_id)
        response = ask(
            chatbot=instance,
            request_id=request_id,
            text=payload["text"],
            userConfig=payload["config"],
        )
        conversation_id = instance.conversation_id
        logging.info(
            f"Saving user_context for {user_chat_id}, engine {engine_type}, conversation_id: {conversation_id}"
        )
        user_context.write(
            user_chat_id=user_chat_id,
            engine=engine_type,
            conversation_id=conversation_id,
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
        logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
