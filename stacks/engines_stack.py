from pathlib import Path

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_iam,
    aws_lambda_event_sources,
    aws_sns,
    aws_sns_subscriptions,
    aws_sqs,
    aws_ssm,
)
from aws_cdk.aws_lambda import DockerImageCode, DockerImageFunction
from constructs import Construct

ASSET_PATH = "engines"


class EnginesStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambda_role = aws_iam.Role(
            self,
            "EnginesLambdaRole",
            assumed_by=aws_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonS3ReadOnlyAccess"
                ),
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMFullAccess"
                ),
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonDynamoDBFullAccess"
                ),
            ],
        )

        # SNS request topic (one for all engines)

        request_topic = aws_sns.Topic(
            self,
            "RequestTopic",
            display_name="Request AI engines topic",
            topic_name="request-ai-topic",
        )
        aws_ssm.StringParameter(
            self,
            "snsRequestTopicParam",
            parameter_name="REQUESTS_SNS_TOPIC_ARN",
            string_value=request_topic.topic_arn,
        )

        request_dlq = aws_sqs.Queue(
            self,
            "Request-Queues-DLQ",
            queue_name="Request-Queues-DLQ",
            removal_policy=RemovalPolicy.DESTROY,
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(3),
            enforce_ssl=True,
        )
        self.dlq = aws_sqs.DeadLetterQueue(
            max_receive_count=1,
            queue=request_dlq,
        )

        # AI Engines Lambdas

        # Bing

        bing_queue = aws_sqs.Queue(
            self,
            "Bing-Request-Queue",
            queue_name="Bing-Request-Queue",
            removal_policy=RemovalPolicy.DESTROY,
            visibility_timeout=Duration.minutes(3),
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=self.dlq,
            enforce_ssl=True,
        )
        request_topic.add_subscription(
            aws_sns_subscriptions.SqsSubscription(
                queue=bing_queue,
                raw_message_delivery=True,
                filter_policy={
                    "type": aws_sns.SubscriptionFilter.string_filter(
                        allowlist=["text"]
                    ),
                    "engines": aws_sns.SubscriptionFilter.string_filter(
                        allowlist=["bing"]
                    ),
                },
            )
        )

        docker_path = str(Path(__file__).parent.parent.joinpath(ASSET_PATH).resolve())
        bing_handler = DockerImageFunction(
            self,
            "BingHandler",
            function_name="BingHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=["bing_gpt.sqs_handler"],
            ),
            timeout=Duration.minutes(3),
            role=lambda_role,
        )
        bing_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(bing_queue)
        )

        # Bard

        bard_queue = aws_sqs.Queue(
            self,
            "Bard-Request-Queue",
            queue_name="Bard-Request-Queue",
            removal_policy=RemovalPolicy.DESTROY,
            visibility_timeout=Duration.minutes(3),
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=self.dlq,
            enforce_ssl=True,
        )
        request_topic.add_subscription(
            aws_sns_subscriptions.SqsSubscription(
                queue=bard_queue,
                raw_message_delivery=True,
                filter_policy={
                    "type": aws_sns.SubscriptionFilter.string_filter(
                        allowlist=["text"]
                    ),
                    "engines": aws_sns.SubscriptionFilter.string_filter(
                        allowlist=["bard"]
                    ),
                },
            )
        )

        bard_handler = DockerImageFunction(
            self,
            "BardHandler",
            function_name="BardHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=["bard_engine.sqs_handler"],
            ),
            timeout=Duration.minutes(3),
            role=lambda_role,
        )

        bard_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(bard_queue)
        )

        # ChatGPT

        chat_queue = aws_sqs.Queue(
            self,
            "ChatGPT-Request-Queue",
            queue_name="ChatGPT-Request-Queue",
            removal_policy=RemovalPolicy.DESTROY,
            visibility_timeout=Duration.minutes(3),
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=self.dlq,
            enforce_ssl=True,
        )
        request_topic.add_subscription(
            aws_sns_subscriptions.SqsSubscription(
                queue=chat_queue,
                raw_message_delivery=True,
                filter_policy={
                    "type": aws_sns.SubscriptionFilter.string_filter(
                        allowlist=["text"]
                    ),
                    "engines": aws_sns.SubscriptionFilter.string_filter(
                        allowlist=["chatgpt"]
                    ),
                },
            )
        )

        chatgpt_handler = DockerImageFunction(
            self,
            "ChatGptHandler",
            function_name="ChatGptHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=["chat_gpt.sqs_handler"],
            ),
            timeout=Duration.minutes(3),
            role=lambda_role,
        )
        chatgpt_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(chat_queue)
        )

        # Dall-E

        dalee_queue = aws_sqs.Queue(
            self,
            "Dalle-Request-Queue",
            queue_name="Dalle-Request-Queue",
            removal_policy=RemovalPolicy.DESTROY,
            visibility_timeout=Duration.minutes(3),
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=self.dlq,
            enforce_ssl=True,
        )
        request_topic.add_subscription(
            aws_sns_subscriptions.SqsSubscription(
                queue=dalee_queue,
                raw_message_delivery=True,
                filter_policy={
                    "type": aws_sns.SubscriptionFilter.string_filter(
                        allowlist=["images"]
                    ),
                },
            )
        )
        dalle_handler = DockerImageFunction(
            self,
            "DalleHandler",
            function_name="DalleHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=["dalle_img.sqs_handler"],
            ),
            timeout=Duration.minutes(3),
            role=lambda_role,
        )
        dalle_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(dalee_queue)
        )
