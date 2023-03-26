import asyncio
import logging

import boto3
from telegram.ext import Application

logging.basicConfig()
logging.getLogger().setLevel("INFO")


async def set_webhook():
    _ssm_client = boto3.client(service_name="ssm")
    token = _ssm_client.get_parameter(Name="TELEGRAM_TOKEN")["Parameter"]["Value"]
    url = _ssm_client.get_parameter(Name="LAMBDA_URL")["Parameter"]["Value"]
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
                ],
                secret_token=secret,
            )
    except Exception as e:
        logging.error(e)


def lambda_handler(event, context):
    return asyncio.get_event_loop().run_until_complete(set_webhook())
