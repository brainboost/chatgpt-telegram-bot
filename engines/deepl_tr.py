import json
import logging
from typing import Any

import boto3
from deepl import Translator

from .common_utils import encode_message, escape_markdown_v2, read_ssm_param

logging.basicConfig()
logging.getLogger().setLevel("INFO")


auth_key = read_ssm_param(param_name="DEEPL_AUTHKEY")

translator = Translator(auth_key)
result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")


def __parse_languages(lang: str) -> list:
    langs = lang.upper().split(",")
    return langs

def __process_payload(payload: Any, request_id: str) -> None:
    # logging.info(payload)
    languages = __parse_languages(payload["languages"])
    for lang in languages:
        try:
            response = translator.translate_text(
                payload["text"].replace("/tr", ""), target_lang=lang.strip()
            )
            result = escape_markdown_v2(response.text)
        except Exception as e:
            logging.error(e)
            result = escape_markdown_v2(str(e))

        payload["engine"] = lang.replace("-", "\-")
        payload["response"] = encode_message(result)
        sns.publish(TopicArn=result_topic, Message=json.dumps(payload))


def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        __process_payload(payload, request_id)
