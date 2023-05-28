import json
import logging

import boto3
import common_utils as utils
from BingImageCreator import ImageGen

logging.basicConfig()
logging.getLogger().setLevel("INFO")


def create() -> ImageGen:
    s3_path = utils.read_ssm_param(param_name="COOKIES_FILE")
    bucket_name, file_name = s3_path.replace("s3://", "").split("/", 1)
    auth_cookies = utils.read_json_from_s3(bucket_name, file_name)
    u = [x.get("value") for x in auth_cookies if x.get("name") == "_U"][0]
    return ImageGen(u)


imageGen = create()
results_queue = utils.read_ssm_param(param_name="RESULTS_SQS_QUEUE_URL")
sqs = boto3.session.Session().client("sqs")


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        logging.info(payload)
        prompt = payload["text"]
        list: list[str] = []
        if prompt is not None and prompt.strip():
            list = imageGen.get_images(prompt)
        else:
            # for testing purposes
            list = [
                "https://picsum.photos/200#1",
                "https://picsum.photos/200#2",
                "https://picsum.photos/200#3",
                "https://picsum.photos/200#4",
            ]
        logging.info(list)
        message = "\n".join(list)
        payload["response"] = utils.encode_message(message)
        payload["engine"] = "Dall-E"
        logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))
