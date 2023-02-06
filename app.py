#!/usr/bin/env python3

import aws_cdk as cdk
from stacks.chatgpt_bot_stack import ChatgptBotStack
from stacks.webhook_stack import WebhookStack

app = cdk.App()
botStack = ChatgptBotStack(app, "ChatgptBotStack")
webhookStack = WebhookStack(app, "WebhookStack", botStack.lambda_layer)
app.synth()
