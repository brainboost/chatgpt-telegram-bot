import json
import logging
import os
import uuid
from functools import wraps

import boto3
import wget
from telegram import constants

logging.basicConfig()
logging.getLogger().setLevel("INFO")


async def send_typing_action(func):
    """Sends typing action while processing func command."""

    @wraps(func)
    async def command_func(update, context, *args, **kwargs):
        await context.bot.send_chat_action(
            chat_id=update.effective_message.chat_id, action=constants.ChatAction.TYPING
        )
        return func(update, context, *args, **kwargs)

    return command_func


def generate_transcription(file):
    s3_client = boto3.client("s3")
    transcribe_client = boto3.client("transcribe")

    local_path = "/tmp/voice_message.ogg"
    message_id = str(uuid.uuid4())

    s3_bucket = read_param(param_name="CHATBOT_S3_BUCKET")
    s3_prefix = os.path.join(message_id, "voice_file.ogg")
    remote_s3_path = os.path.join("s3://", s3_bucket, s3_prefix)

    file.download(local_path)
    s3_client.upload_file(local_path, s3_bucket, s3_prefix)

    job_name = f"transcription_job_{message_id}"
    transcribe_client.start_transcription_job(
        TranscriptionJobName=job_name,
        IdentifyLanguage=True,
        MediaFormat="ogg",
        Media={"MediaFileUri": remote_s3_path},
    )

    job_status = None
    while job_status != "COMPLETED":
        status = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
        job_status = status["TranscriptionJob"]["TranscriptionJobStatus"]

    transcript = status["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
    logging.info(transcript)

    output_location = f"/tmp/output_{message_id}.json"
    wget.download(transcript, output_location)

    with open(output_location) as f:
        output = json.load(f)
    return output["results"]["transcripts"][0]["transcript"]

    
def read_param(param_name: str) -> str:
    ssm_client = boto3.client(service_name="ssm")     
    return ssm_client.get_parameter(Name=param_name)[
          "Parameter"
          ]["Value"]

def read_json_from_s3(bucket_name: str, file_name: str) -> dict:
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket_name, Key=file_name)
    file_content = response["Body"].read().decode("utf-8")
    return json.loads(file_content)
