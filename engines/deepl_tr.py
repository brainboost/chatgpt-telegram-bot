import json
import logging
import re

import boto3
import common_utils as utils
from deepl import Translator

logging.basicConfig()
logging.getLogger().setLevel("INFO")


auth_key = utils.read_ssm_param(param_name="DEEPL_AUTHKEY")

translator = Translator(auth_key)
results_queue = utils.read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")
__esc_pattern = re.compile(f"(?<!\|)([{re.escape(r'._-+#|{}!=()<>')}])(?!\|)")


def __parse_languages(lang: str) -> list:
    langs = lang.upper().split(",")
    return langs


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        logging.info(payload)
        languages = __parse_languages(payload["languages"])
        for lang in languages:
            try:
                response = translator.translate_text(
                    payload["text"].replace("/tr", ""), target_lang=lang.strip()
                )
                result = re.sub(
                    pattern=__esc_pattern, repl=r"\\\1", string=response.text
                )
            except Exception as e:
                logging.error(e)
                result = str(e)

            payload["engine"] = lang
            payload["response"] = utils.encode_message(result)
            logging.info(payload)
            sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))