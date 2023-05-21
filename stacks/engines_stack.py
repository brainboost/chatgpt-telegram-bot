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
from aws_cdk import aws_lambda as _lambda
from aws_cdk.aws_lambda_python_alpha import PythonFunction, PythonLayerVersion
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

        lambda_layer = PythonLayerVersion(
            self,
            "EnginesLambdaLayer",
            entry=ASSET_PATH,
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_9],
        )

        # AI Engines Lambdas

        # Bing

        request_dlq = aws_sqs.Queue(
            self,
            "Request-Queues-DLQ",
            queue_name="Request-Queues-DLQ",
            removal_policy=RemovalPolicy.DESTROY,
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(3),
        )
        request_dlq.add_to_resource_policy(
            self.get_enforce_ssl_policy(request_dlq.queue_arn)
        )
        self.dlq = aws_sqs.DeadLetterQueue(
            max_receive_count=1,
            queue=request_dlq,
        )
        bing_queue = aws_sqs.Queue(
            self,
            "Bing-Request-Queue",
            queue_name="Bing-Request-Queue",
            removal_policy=RemovalPolicy.DESTROY,
            visibility_timeout=Duration.seconds(900),
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=self.dlq,
        )
        bing_queue.add_to_resource_policy(
            self.get_enforce_ssl_policy(bing_queue.queue_arn)
        )
        request_topic.add_subscription(
            aws_sns_subscriptions.SqsSubscription(
                queue=bing_queue,
                raw_message_delivery=True,
                filter_policy={
                    "engines": aws_sns.SubscriptionFilter.string_filter(
                        allowlist=["bing"]
                    ),
                },
            )
        )
        bing_handler = PythonFunction(
            self,
            "BingHandler",
            entry=ASSET_PATH,
            runtime=_lambda.Runtime.PYTHON_3_9,
            index="bing_gpt.py",
            handler="sqs_handler",
            layers=[lambda_layer],
            timeout=Duration.minutes(5),
            role=lambda_role,
            dead_letter_queue=request_dlq,
        )
        bing_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(bing_queue)
        )

    def get_enforce_ssl_policy(self, queue_arn):
        return aws_iam.PolicyStatement(
            sid="Enforce TLS for all principals",
            effect=aws_iam.Effect.DENY,
            principals=[
                aws_iam.AnyPrincipal(),
            ],
            actions=[
                "sqs:*",
            ],
            resources=[queue_arn],
            conditions={
                "Bool": {"aws:SecureTransport": "false"},
            },
        )
