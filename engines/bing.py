import asyncio
import json
import logging
import math
import os
import random
import re
import string
from pathlib import Path
from typing import Any

import boto3
from aiohttp import ClientSession
from EdgeGPT.EdgeGPT import CONVERSATION_STYLE_TYPE, Chatbot

try:
    from PIL.Image import Image
except ImportError:
    Image = type

from .common_utils import (
    encode_message,
    escape_markdown_v2,
    get_image,
    read_json_from_s3,
    read_ssm_param,
)
from .image_utils import (
    ImageResponse,
    ImageType,
    process_image,
    to_base64_jpg,
    to_image,
)
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")

engine_type = "bing"
remove_links_pattern = re.compile(r"\[\^\d+\^\]\s?")
ref_link_pattern = re.compile(r"\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?")
image_config = {
    "maxImagePixels": 360000,
    "imageCompressionRate": 0.7,
    "enableFaceBlurDebug": 0,
}


def process_command(input: str, context: UserContext) -> None:
    command = input.removeprefix(prefix="/").lower()
    logging.info(f"Processing command {command} for {context.user_id}")
    if "reset" in command:
        context.reset_conversation()
        logging.info(f"Conversation hass been reset for {context.user_id}")
        return
    logging.error(f"Unknown command {command}")


def build_image_upload_api_payload(image_bin: str, tone: str):
    payload = {
        "invokedSkills": ["ImageById"],
        "subscriptionId": "Bing.Chat.Multimodal",
        "invokedSkillsRequestData": {"enableFaceBlur": True},
        "convoData": {"convoid": "", "convotone": tone},
    }
    knowledge_request = {"imageInfo": {}, "knowledgeRequest": payload}
    boundary = "----WebKitFormBoundary" + "".join(
        random.choices(string.ascii_letters + string.digits, k=16)
    )
    data = (
        f"--{boundary}"
        + '\r\nContent-Disposition: form-data; name="knowledgeRequest"\r\n\r\n'
        + json.dumps(knowledge_request, ensure_ascii=False)
        + "\r\n--"
        + boundary
        + '\r\nContent-Disposition: form-data; name="imageBase64"\r\n\r\n'
        + image_bin
        + "\r\n--"
        + boundary
        + "--\r\n"
    )
    return data, boundary


async def upload_image(
    session: ClientSession, image: ImageType, tone: str, proxy: str = None
) -> ImageResponse:
    image = to_image(image)
    width, height = image.size
    max_image_pixels = image_config["maxImagePixels"]
    if max_image_pixels / (width * height) < 1:
        new_width = int(width * math.sqrt(max_image_pixels / (width * height)))
        new_height = int(height * math.sqrt(max_image_pixels / (width * height)))
    else:
        new_width = width
        new_height = height
    new_img = process_image(image, new_width, new_height)
    new_img_binary_data = to_base64_jpg(new_img, image_config["imageCompressionRate"])
    data, boundary = build_image_upload_api_payload(new_img_binary_data, tone)
    headers = session.headers.copy()
    headers["content-type"] = f"multipart/form-data; boundary={boundary}"
    headers["referer"] = "https://www.bing.com/search?q=Bing+AI&showconv=1&FORM=hpcodx"
    headers["origin"] = "https://www.bing.com"
    async with session.post(
        "https://www.bing.com/images/kblob", data=data, headers=headers, proxy=proxy
    ) as response:
        if response.status != 200:
            raise RuntimeError("Failed to upload image.")
        image_info = await response.json()
        if not image_info.get("blobId"):
            raise RuntimeError("Failed to parse image info.")
        result = {"bcid": image_info.get("blobId", "")}
        result["blurredBcid"] = image_info.get("processedBlobId", "")
        if result["blurredBcid"] != "":
            result["imageUrl"] = (
                "https://www.bing.com/images/blob?bcid=" + result["blurredBcid"]
            )
        elif result["bcid"] != "":
            result["imageUrl"] = (
                "https://www.bing.com/images/blob?bcid=" + result["bcid"]
            )
        result["originalImageUrl"] = (
            "https://www.bing.com/images/blob?bcid=" + result["blurredBcid"]
            if image_config["enableFaceBlurDebug"]
            else "https://www.bing.com/images/blob?bcid=" + result["bcid"]
        )
        return ImageResponse(result["imageUrl"], "", result)


def ask(
    text: str,
    chatbot: Chatbot,
    style: CONVERSATION_STYLE_TYPE,
    file_path: str,
    context: UserContext,
) -> str:
    if "/ping" in text:
        return "pong"

    if file_path:
        logging.info(f"Downloading image '{file_path}'")
        image = get_image(file_path, bucket_name)
        imgResp = await upload_image(
            session=None,
            image=Path(image).read_bytes(),
            tone="balanced",
            proxy=os.environ["all_proxy"],
        )
        logging.info(imgResp.images)
        response = asyncio.run(chatbot.ask(prompt=text))
    else:
        response = asyncio.run(
            chatbot.ask(
                prompt=text,
                conversation_style=style,
                webpage_context=context.conversation_id,
                simplify_response=False,
            ),
        )
    logging.info(response)
    item = response["item"]
    context.conversation_id = item["conversationId"]
    try:
        return __as_markdown(item)
    except Exception as e:
        logging.error(
            f"chatbot.ask finished with an error. Request:{text}, response item:{item}",
            exc_info=e,
        )
        return __as_plaintext(item)


def __as_plaintext(item: dict) -> str:
    return re.sub(
        pattern=remove_links_pattern,
        repl="",
        string=item["messages"][1]["text"],
    )


def __as_markdown(item: dict) -> str:
    message = item["messages"][-1]
    text = message["adaptiveCards"][0]["body"][0]["text"]
    return __replace_references(text)


def __replace_references(text: str) -> str:
    ref_links = re.findall(pattern=ref_link_pattern, string=text)
    text = re.sub(pattern=ref_link_pattern, repl="", string=text)
    text = escape_markdown_v2(text)
    for link in ref_links:
        link_label = link[0]
        link_ref = link[1]
        inline_link = f" [\[{link_label}\]]({link_ref})"
        text = re.sub(
            pattern=rf"\[\^{link_label}\^\]\[\d+\]", repl=inline_link, string=text
        )
    return text


def create() -> Chatbot:
    logging.info("Create chatbot instance")
    bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
    socks_url = read_ssm_param(param_name="SOCKS5_URL")
    os.environ["all_proxy"] = socks_url
    chatbot = asyncio.run(
        Chatbot.create(cookies=read_json_from_s3(bucket_name, "bing-cookies.json"))
    )
    return chatbot


bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
sns = boto3.session.Session().client("sns")


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

    instance = create()
    style = payload["config"].get("style", "creative")
    response = ask(
        text=payload["text"],
        chatbot=instance,
        style=style,
        context=user_context,
    )
    user_context.save_conversation(
        conversation={"request": payload["text"], "response": response},
    )
    payload["response"] = encode_message(response)
    payload["engine"] = engine_type
    sns.publish(TopicArn=result_topic, Message=json.dumps(payload))


def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logging.info(f"Request ID: {request_id}")
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        __process_payload(payload, request_id)
