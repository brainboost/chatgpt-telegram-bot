import json
import logging

import boto3
import common_utils as utils
from BingImageCreator import ImageGen
from conversation_history import ConversationHistory

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
history = ConversationHistory()


def sqs_handler(event, context):
    for record in event["Records"]:
        payload = json.loads(record["body"])
        prompt = payload["text"]
        list = []
        if prompt is not None and prompt.strip():
            try:
                list = imageGen.get_images(prompt)
            except Exception as e:
                if "prompt has been blocked" in str(e):
                    list = [str(e)]
                else:
                    logging.error(e)
                    logging.info(payload)
        else:
            # for testing purposes
            list = [
                "https://picsum.photos/200#1",
                "https://picsum.photos/200#2",
                "https://picsum.photos/200#3",
                "https://picsum.photos/200#4",
            ]
        payload["response"] = list
        user_id = payload["user_id"]
        conversation_id = f"{payload['chat_id']}_{user_id}_{payload['update_id']}"
        try:
            history.write(
                conversation_id=conversation_id,
                user_id=user_id,
                conversation=payload,
            )
        except Exception as e:
            logging.error(
                f"conversation_id: {conversation_id}, error: {e}, item: {payload}"
            )

        logging.info(list)
        message = "\n".join(list)
        payload["response"] = utils.encode_message(message)
        payload["engine"] = "Dall-E"
        logging.info(payload)
        sqs.send_message(QueueUrl=results_queue, MessageBody=json.dumps(payload))