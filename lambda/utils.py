import uuid
import os
import json
import boto3
import wget
import logging
from telegram import constants
from functools import wraps

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

    s3_bucket = boto3.client(service_name="ssm").get_parameter(Name="CHATBOT_S3_BUCKET")
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
