from pathlib import Path

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudwatch,
    aws_cloudwatch_actions,
    aws_iam,
    aws_lambda_event_sources,
    aws_logs,
    aws_sns,
    aws_sqs,
    aws_ssm,
)
from aws_cdk import aws_lambda as _lambda
from constructs import Construct

ASSET_PATH = "engines"


def engines_bundle_dir() -> str:
    """Staging dir produced by scripts/build_bundles.py (code + deps, no Docker)."""
    return str(Path(__file__).resolve().parent.parent / "build" / "bundles" / "engines")


class EnginesStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.lambda_role = aws_iam.Role(
            self,
            "EnginesLambdaRole",
            assumed_by=aws_iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonS3FullAccess"
                ),
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMFullAccess"
                ),
                aws_iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonDynamoDBFullAccess"
                ),
            ],
        )
        self.lambda_role.add_to_policy(
            aws_iam.PolicyStatement(
                actions=[
                    "sqs:SendMessage",
                    "sqs:DeleteMessage",
                    "sns:ReceiveMessage",
                    "sns:Publish",
                ],
                resources=["*"],
            )
        )

        # SNS request topic (one for all engines)

        self.request_topic = aws_sns.Topic(
            self,
            "RequestTopic",
            display_name="Request AI engines topic",
            topic_name="request-ai-topic",
        )
        aws_ssm.StringParameter(
            self,
            "snsRequestTopicParam",
            parameter_name="REQUESTS_SNS_TOPIC_ARN",
            string_value=self.request_topic.topic_arn,
        )
        self.dlq = aws_sqs.Queue(
            self,
            "Request-Queues-DLQ",
            queue_name="Request-Queues-DLQ",
            removal_policy=RemovalPolicy.DESTROY,
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(5),
            enforce_ssl=True,
        )
        self.alarm_topic = aws_sns.Topic(
            self,
            "EnginesErrorsAlarms",
            topic_name="EnginesErrorsAlarms",
            display_name="Engines Errors Alarms Topic",
        )
        notify_email = aws_ssm.StringParameter.value_for_string_parameter(
            self, "ALARM_EMAIL"
        )
        aws_sns.Subscription(
            self,
            "EnginesAlarmEmailSubscription",
            topic=self.alarm_topic,
            endpoint=notify_email,
            protocol=aws_sns.SubscriptionProtocol.EMAIL,
        )

        request_dlq_alarm = aws_cloudwatch.Alarm(
            self,
            "ResultDlqAlarm",
            alarm_name="RequestDlqAlarm",
            alarm_description="Alarm when Request DLQ queue has messages",
            metric=self.dlq.metric_approximate_number_of_messages_visible(),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        )
        request_dlq_alarm.add_alarm_action(
            aws_cloudwatch_actions.SnsAction(self.alarm_topic)
        )

        # AI Engine Lambdas (ZIP code assets; runtime-managed Python, no ECR/images)

        # DeepL

        deepl_log_group = aws_logs.LogGroup(
            self,
            "DeepLHandlerLogGroup",
            log_group_name="/aws/lambda/DeepLHandler",
            retention=aws_logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.__create_engine(
            engine_name="DeepL",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["translate"]
                ),
            },
            handler=f"{ASSET_PATH}.deepl_tr.sns_handler",
            log_group=deepl_log_group,
        )

        # LLama 4 (Ollama Cloud)

        llama_log_group = aws_logs.LogGroup(
            self,
            "LLamaHandlerLogGroup",
            log_group_name="/aws/lambda/LLamaHandler",
            retention=aws_logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.__create_engine(
            engine_name="LLama",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["text", "command"]
                ),
                "engines": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["llama"]
                ),
            },
            handler=f"{ASSET_PATH}.ollama.sns_handler",
            log_group=llama_log_group,
            environment={
                "OLLAMA_ENGINE": "llama",
                "OLLAMA_MODEL": "llama4:maverick",
            },
        )

        # Qwen 3.5 (Ollama Cloud)

        qwen_log_group = aws_logs.LogGroup(
            self,
            "QwenHandlerLogGroup",
            log_group_name="/aws/lambda/QwenHandler",
            retention=aws_logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.__create_engine(
            engine_name="Qwen",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["text", "command"]
                ),
                "engines": aws_sns.SubscriptionFilter.string_filter(allowlist=["qwen"]),
            },
            handler=f"{ASSET_PATH}.ollama.sns_handler",
            log_group=qwen_log_group,
            environment={
                "OLLAMA_ENGINE": "qwen",
                "OLLAMA_MODEL": "qwen3.5:cloud",
            },
        )

        # Ideogram

        ideogram_log_group = aws_logs.LogGroup(
            self,
            "IdeogramHandlerLogGroup",
            log_group_name="/aws/lambda/IdeogramHandler",
            retention=aws_logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.__create_engine(
            engine_name="Ideogram",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["ideogram"]
                ),
            },
            handler=f"{ASSET_PATH}.ideogram_img.sns_handler",
            log_group=ideogram_log_group,
        )

        # Add ideogram result queue with delayed message to retrieve results when ready
        resultQueue = aws_sqs.Queue(
            self,
            "Ideogram-Result-Queue",
            queue_name="Ideogram-Result-Queue",
            removal_policy=RemovalPolicy.DESTROY,
            visibility_timeout=Duration.seconds(5),
            delivery_delay=Duration.seconds(5),
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )
        # Create log group for ideogram result handler
        ideogram_result_log_group = aws_logs.LogGroup(
            self,
            "IdeogramResultHandlerLogGroup",
            log_group_name="/aws/lambda/IdeogramResultHandler",
            retention=aws_logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        resultHandler = _lambda.Function(
            self,
            "IdeogramResultHandler",
            function_name="IdeogramResultHandler",
            runtime=_lambda.Runtime.PYTHON_3_14,
            code=_lambda.Code.from_asset(engines_bundle_dir()),
            handler=f"{ASSET_PATH}.ideogram_result.sqs_handler",
            log_group=ideogram_result_log_group,
            role=self.lambda_role,
            dead_letter_queue_enabled=True,
            dead_letter_queue=self.dlq,
        )
        resultHandler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(resultQueue)
        )

        # Gemini

        gemini_log_group = aws_logs.LogGroup(
            self,
            "GeminiHandlerLogGroup",
            log_group_name="/aws/lambda/GeminiHandler",
            retention=aws_logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.__create_engine(
            engine_name="Gemini",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["text", "command"]
                ),
                "engines": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["gemini"]
                ),
            },
            handler=f"{ASSET_PATH}.gemini.sns_handler",
            log_group=gemini_log_group,
        )

    def __create_engine(
        self,
        engine_name: str,
        sns_filter_policy: any,
        handler: str,
        log_group: aws_logs.LogGroup,
        environment: dict = None,
    ) -> None:
        """Creates infrastructure for the AI engine handler (queue-lambda-alarm)."""

        lambda_config = {
            "timeout": Duration.minutes(5),
            "memory_size": 256,
            "log_group": log_group,
            "role": self.lambda_role,
            "dead_letter_queue_enabled": True,
            "dead_letter_queue": self.dlq,
        }
        if environment:
            lambda_config["environment"] = environment

        lambda_fn = _lambda.Function(
            self,
            f"{engine_name}Handler",
            function_name=f"{engine_name}Handler",
            runtime=_lambda.Runtime.PYTHON_3_14,
            code=_lambda.Code.from_asset(engines_bundle_dir()),
            handler=handler,
            **lambda_config,
        )
        lambda_fn.add_event_source(
            aws_lambda_event_sources.SnsEventSource(
                topic=self.request_topic,
                filter_policy=sns_filter_policy,
            )
        )
