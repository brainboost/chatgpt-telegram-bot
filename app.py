#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.chatbot_stack import ChatBotStack
from stacks.database_stack import DatabaseStack
from stacks.engines_stack import EnginesStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_ACCOUNT", None),
    region=os.environ.get("CDK_REGION", None),
)
stage = os.environ.get("STAGE", "dev")

engStack = EnginesStack(
    scope=app,
    construct_id="EnginesStack",
    description="A stack that creates lambda functions working with AI engines APIs",
    env=env,
)

databaseStack = DatabaseStack(
    scope=app,
    construct_id="DatabaseStack",
    description="A stack containing database",
    env=env,
)

botStack = ChatBotStack(
    scope=app,
    construct_id="ChatBotStack",
    description="A stack containing telegram bot lambda",
    env=env,
    stage=stage,
)
botStack.add_dependency(engStack)
botStack.add_dependency(databaseStack)

app.synth()
