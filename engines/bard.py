import json
import logging
import re
from typing import Any, Optional

import boto3
import requests
from bardapi import Bard

from .common_utils import encode_message, read_json_from_s3, read_ssm_param, save_to_s3
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
result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")


def process_command(input: str, context: UserContext) -> None:
    command = input.removeprefix(prefix="/").lower()
    logging.info(f"Processing command {command} for {context.user_id}")
    if "reset" in command:
        context.reset_conversation()
        logging.info(f"Conversation hass been reset for {context.user_id}")
        return
    logging.error(f"Unknown command {command}")


def ask(
    text: str,
    chatbot: Bard,
    context: UserContext,
) -> str:
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
    context.conversation_id = chatbot.conversation_id
    answer = __as_markdown(item)
    return answer


def create(conversation_id: Optional[str]) -> Bard:
    logging.info("Create chatbot instance")
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
        f"1PSID {psid}, proxy: {socks_url}, cookies: {len(session.cookies.items())}"
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

    instance = create(conversation_id=user_context.conversation_id)
    response = ask(
        text=payload["text"],
        chatbot=instance,
        context=user_context,
    )
    user_context.save_conversation(
        conversation={"request": payload["text"], "response": response},
    )
    payload["response"] = encode_message(response)
    payload["engine"] = engine_type
    logging.info(payload)
    sns.publish(TopicArn=result_topic, Message=json.dumps(payload))


def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        __process_payload(payload, request_id)

