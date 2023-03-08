#!/usr/bin/env python3

import aws_cdk as cdk
from stacks.chatgpt_bot_stack import ChatgptBotStack

# from stacks.webhook_stack import WebhookStack

app = cdk.App()
botStack = ChatgptBotStack(app, "ChatgptBotStack")
# webhookStack = WebhookStack(
#     app,
#     "WebhookStack",
#     botStack.lambda_layer.layer_version_arn,
#     botStack.lambda_function_url,
#     botStack.bucket.bucket_name,
# )
# webhookStack.add_dependency(botStack)
app.synth()
