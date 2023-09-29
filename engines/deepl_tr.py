import json
import logging

import boto3
from deepl import Translator

from .common_utils import encode_message, escape_markdown_v2, read_ssm_param

logging.basicConfig()
logging.getLogger().setLevel("INFO")


auth_key = read_ssm_param(param_name="DEEPL_AUTHKEY")

translator = Translator(auth_key)
results_queue = read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")


def __parse_languages(lang: str) -> list:
    langs = lang.upper().split(",")
    return langs


def sqs_handler(event, context):
    """AWS SQS event handler"""
    for record in event["Records"]:
        payload = json.loads(record["body"])
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
            sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
