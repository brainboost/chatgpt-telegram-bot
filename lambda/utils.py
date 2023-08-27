import base64
import json
import logging
import os
import re
import uuid
import zlib
from functools import wraps

import boto3
import wget
from telegram import Update, constants

logging.basicConfig()
logging.getLogger().setLevel("INFO")

ref_link_pattern = re.compile(r"\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?")
esc_pattern = re.compile(f"(?<!\|)([{re.escape(r'.-+#|{}!=()<>')}])(?!\|)")


def send_action(action):
    """Sends `action` while processing func command."""

    def decorator(func):
        @wraps(func)
        async def command_func(update: Update, context, *args, **kwargs):
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=action
            )
            return await func(update, context, *args, **kwargs)

        return command_func

    return decorator


send_typing_action = send_action(constants.ChatAction.TYPING)


def generate_transcription(file):
    s3_client = boto3.client("s3")
    transcribe_client = boto3.client("transcribe")

    local_path = "/tmp/voice_message.ogg"
    message_id = str(uuid.uuid4())

    s3_bucket = read_ssm_param(param_name="BOT_S3_BUCKET")
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


def read_ssm_param(param_name: str):
    ssm_client = boto3.client(service_name="ssm")
    return ssm_client.get_parameter(Name=param_name)["Parameter"]["Value"]


def escape_markdown_v2(text: str) -> str:
    return re.sub(pattern=esc_pattern, repl=r"\\\1", string=text)


def decode_message(encoded: str) -> str:
    bin = base64.b64decode(encoded.encode("ascii"))
    unzipped = zlib.decompress(bin)
    return unzipped.decode("utf-8")


def recursive_stringify(arr) -> str:
    result = []
    for item in arr:
        if isinstance(item, list):
            result.append(recursive_stringify(item))
        else:
            result.append(str(item))
    return ", ".join(result) + "\n"


def split_long_message(message: str, header: str, max_length: int) -> list:
    result = []
    length = len(message)
    start = 0
    end = min(max_length, length)
    count = length // max_length + 1
    i = 1
    while start < length:
        text = f"{header}: {i} of {count}\n{message[start:end]}"
        result.append(text)
        start = end
        end = min(start + max_length, length)
        i += 1
    return result
