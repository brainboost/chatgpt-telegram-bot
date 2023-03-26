#!/usr/bin/env python3

import aws_cdk as cdk

from stacks.chatgpt_bot_stack import ChatgptBotStack
from stacks.database_stack import DatabaseStack

app = cdk.App()
botStack = ChatgptBotStack(app, "ChatgptBotStack")
databaseStack = DatabaseStack(app, "DatabaseStack")
app.synth()
