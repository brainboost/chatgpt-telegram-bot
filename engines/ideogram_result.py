import json
import logging

import boto3
from curl_cffi import requests

from .session import build_context, publish_result

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

engine_type = "ideogram"
threshold_img_quality = 1024
base_url = "https://ideogram.ai"
browser_version = "chrome120"
retrieve_metadata_url = f"{base_url}/api/images/retrieve_metadata_request_id/"
get_images_url = f"{base_url}/api/images/direct/"

sqs = boto3.session.Session().client("sqs")


class IdeogramError(Exception):
    """A failed Ideogram result poll; raised so the DLQ can catch it."""


def retrieve_images(payload: dict) -> str | None:
    logger.info(payload)
    result_id = payload["result_id"]
    if not result_id:
        raise IdeogramError("Cannot get result_id")

    response = requests.get(
        url=retrieve_metadata_url + result_id,
        headers=payload["headers"],
        impersonate=browser_version,
    )
    if not response.ok:
        logger.info(response)
        raise IdeogramError(f"Cannot retrieve images for result_id {result_id}")

    resp_obj = response.json()
    if "resolution" not in resp_obj or resp_obj["resolution"] < threshold_img_quality:
        logger.info("Republishing results %s to achieve delay...", result_id)
        queue_url = payload["queue_url"]
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(payload))
        return None

    urls = []
    for item in resp_obj["responses"]:
        urls.append(get_images_url + item["response_id"])

    logger.info(urls)
    return "\n".join(urls)


def sqs_handler(event, context):
    """AWS SQS event handler: poll Ideogram results and publish them."""
    request_id = context.aws_request_id
    logger.info("Request ID: %s", request_id)
    for record in event["Records"]:
        payload = json.loads(record["body"])
        message = retrieve_images(payload=payload)
        if not message:
            return

        user_id = payload["user_id"]
        result_id = payload["result_id"]
        user_context = build_context(
            payload,
            request_id=result_id,
            engine_label=engine_type,
        )
        user_context.conversation_id = result_id
        try:
            user_context.save_conversation(
                conversation=payload,
            )
        except Exception as e:  # a failed save must not drop the image reply
            logger.error(
                "Saving conversation error. User_id: %s_%s, item: %s",
                user_id,
                payload["chat_id"],
                payload,
                exc_info=e,
            )
        publish_result(payload, engine_type, message)
