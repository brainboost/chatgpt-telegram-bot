import asyncio
import logging

import boto3
import requests
from telegram.ext import Application

logging.basicConfig()
logging.getLogger().setLevel("INFO")


async def set_webhook():
    _ssm_client = boto3.client(service_name="ssm")
    token = _ssm_client.get_parameter(Name="TELEGRAM_TOKEN")["Parameter"]["Value"]
    url = _ssm_client.get_parameter(Name="BOT_LAMBDA_URL")["Parameter"]["Value"]
    secret = _ssm_client.get_parameter(Name="SECRET_TOKEN")["Parameter"]["Value"]
    application = Application.builder().token(token=token).build()
    try:
        webhookInfo = await application.bot.get_webhook_info()
        logging.info(f"current webhook: {webhookInfo.url}")
        if webhookInfo.url != url:
            logging.info(f"setting webhook to {url}")
            await application.bot.set_webhook(
                url=url,
                max_connections=20,
                drop_pending_updates=True,
                allowed_updates=[
                    "update_id",
                    "message",
                    "edited_message",
                    "channel_post",
                    "callback_query",
                ],
                secret_token=secret,
            )
        __update_hash_monster_callback_url(_ssm_client)

    except Exception as e:
        logging.error(e)


def lambda_handler(event, context):
    return asyncio.get_event_loop().run_until_complete(set_webhook())


def __update_hash_monster_callback_url(ssm_client) -> None:
    MONSTERAPI_CALLBACK_URL = "MONSTERAPI_CALLBACK_URL"
    MONSTERAPI_TOKEN = "MONSTERAPI_TOKEN"
    logging.info("Checking existing MonsterApi callbacks")
    callback = ssm_client.get_parameter(Name=MONSTERAPI_CALLBACK_URL)["Parameter"][
        "Value"
    ]
    token = ssm_client.get_parameter(Name=MONSTERAPI_TOKEN)["Parameter"]["Value"]
    headers = {"accept": "application/json", "authorization": f"Bearer {token}"}
    url = "https://api.monsterapi.ai/v1/webhook"
    response = requests.get(url, headers=headers)
    if not response.ok:
        logging.error(
            f"Cannot request MonsterApi callbacks, {response.status_code}, {response.reason}"
        )
        return

    items = response.json()
    logging.info(f"Found {len(items)} callback items")
    if any(item["url_name"] == callback for item in items):
        logging.info(f"Callback '{callback}' already exists, no need to update")
        return

    if any(item["webhook_url"] == callback for item in items):
        logging.info(
            f"Callback URL '{callback}'already exists, updating it with hashed name"
        )
        for item in items:
            if item["webhook_url"] == callback:
                url_name = item["url_name"]
                logging.info(
                    f"Set '{url_name}' as URL name for callback '{callback}' in '{MONSTERAPI_CALLBACK_URL}' SSM parameter"
                )
                ssm_client.put_parameter(
                    Name=MONSTERAPI_CALLBACK_URL,
                    Value=url_name,
                    Type="String",
                    Overwrite=True,
                )
                return

    hashed = hex(hash(str(callback)))
    logging.info(f"Hashing MonsterApi webhook '{callback}' to name '{hashed}'")
    payload = {
        "webhook_url": callback,
        "url_name": hashed,
    }
    post_response = requests.post(url, json=payload, headers=headers)
    if post_response.ok:
        logging.info(
            f"Set '{hashed}' as URL name for callback '{callback}' in '{MONSTERAPI_CALLBACK_URL}' SSM parameter"
        )
        ssm_client.put_parameter(
            Name=MONSTERAPI_CALLBACK_URL, Value=hashed, Type="String", Overwrite=True
        )
        return
    error = f"Cannot setup MonsterApi callback {hashed}, status:{response.status_code}, {response.reason}"
    logging.error(error)
    raise Exception(error)
