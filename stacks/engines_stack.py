from pathlib import Path

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudwatch,
    aws_cloudwatch_actions,
    aws_iam,
    aws_lambda_event_sources,
    aws_sns,
    aws_sns_subscriptions,
    aws_sqs,
    aws_ssm,
)
from aws_cdk import aws_lambda as _lambda
from aws_cdk.aws_lambda import DockerImageCode, DockerImageFunction
from constructs import Construct

ASSET_PATH = "engines"


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
        self.docker_file_path = str(Path(__file__).parent.parent.resolve())

        # AI Engine Lambdas

        # Bing

        self.__create_engine(
            engine_name="Bing",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(allowlist=["text"]),
                "engines": aws_sns.SubscriptionFilter.string_filter(allowlist=["bing"]),
            },
            handler=f"{ASSET_PATH}.bing_gpt.sqs_handler",
        )

        # Bard

        self.__create_engine(
            engine_name="Bard",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(allowlist=["text"]),
                "engines": aws_sns.SubscriptionFilter.string_filter(allowlist=["bard"]),
            },
            handler=f"{ASSET_PATH}.bard_engine.sqs_handler",
        )

        # ChatGPT

        self.__create_engine(
            engine_name="ChatGpt",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(allowlist=["text"]),
                "engines": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["chatgpt"]
                ),
            },
            handler=f"{ASSET_PATH}.chat_gpt.sqs_handler",
        )

        # Dall-E

        self.__create_engine(
            engine_name="Dalle",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(allowlist=["imagine"]),
            },
            handler=f"{ASSET_PATH}.dalle_img.sqs_handler",
        )

        # DeepL

        self.__create_engine(
            engine_name="DeepL",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["translate"]
                ),
            },
            handler=f"{ASSET_PATH}.deepl_tr.sqs_handler",
        )

        # LLama2

        self.__create_engine(
            engine_name="LLama",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(allowlist=["text"]),
                "engines": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["llama"]
                ),
            },
            handler=f"{ASSET_PATH}.monsterapi.sqs_handler",
        )

        # Add monsterapi callback handler

        api_callback = DockerImageFunction(
            self,
            "MonsterApiCallbackHandler",
            function_name="MonsterApiCallbackHandler",
            code=DockerImageCode.from_image_asset(
                directory=self.docker_file_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=[f"{ASSET_PATH}.monsterapi_result.callback_handler"],
            ),
            timeout=Duration.minutes(3),
            memory_size=256,
            role=self.lambda_role,
        )
        error_alarm = aws_cloudwatch.Alarm(
            self,
            "MonsterApiCallbackHandlerErrors",
            alarm_name="MonsterApiCallbackHandlerErrors",
            metric=api_callback.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=aws_cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Alarm when MonsterApi callback lambda has errors",
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )
        error_alarm.add_alarm_action(aws_cloudwatch_actions.SnsAction(self.alarm_topic))
        callback_lambda_url = api_callback.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE,
            cors={
                "allowed_origins": ["https://*"],
                "allowed_methods": [_lambda.HttpMethod.POST],
            },
        )
        aws_ssm.StringParameter(
            self,
            "MonsterApiCallbackURLParam",
            parameter_name="MONSTERAPI_CALLBACK_URL",
            string_value=callback_lambda_url.url,
        )

        # Ideogram

        self.__create_engine(
            engine_name="Ideogram",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["ideogram"]
                ),
            },
            handler=f"{ASSET_PATH}.ideogram.sqs_handler",
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
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=1,
                queue=aws_sqs.Queue(
                    self,
                    "Ideogram-Result-Queue-DLQ",
                    queue_name="Ideogram-Result-Queue-DLQ",
                    removal_policy=RemovalPolicy.DESTROY,
                    encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
                    retention_period=Duration.days(3),
                    enforce_ssl=True,
                ),
            ),
            enforce_ssl=True,
        )
        resultHandler = DockerImageFunction(
            self,
            "IdeogramResultHandler",
            function_name="IdeogramResultHandler",
            code=DockerImageCode.from_image_asset(
                directory=self.docker_file_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=[f"{ASSET_PATH}.ideogram_result.sqs_handler"],
            ),
            # timeout=Duration.minutes(1),
            # memory_size=256,
            role=self.lambda_role,
        )
        resultHandler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(resultQueue)
        )

        # Claude

        self.__create_engine(
            engine_name="Claude",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(allowlist=["text"]),
                "engines": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["claude"]
                ),
            },
            handler=f"{ASSET_PATH}.claude.sqs_handler",
        )

    def __create_engine(
        self,
        engine_name: str,
        sns_filter_policy: any,
        handler: str,
    ) -> None:
        """Creates infrastructure for the AI engine handler (queue-lambda-alarm)."""

        queue = aws_sqs.Queue(
            self,
            f"{engine_name}-Request-Queue",
            queue_name=f"{engine_name}-Request-Queue",
            removal_policy=RemovalPolicy.DESTROY,
            visibility_timeout=Duration.minutes(3),
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=self.dlq,
            enforce_ssl=True,
        )
        self.request_topic.add_subscription(
            aws_sns_subscriptions.SqsSubscription(
                queue=queue,
                raw_message_delivery=True,
                filter_policy=sns_filter_policy,
            )
        )
        lambda_fn = DockerImageFunction(
            self,
            f"{engine_name}Handler",
            function_name=f"{engine_name}Handler",
            code=DockerImageCode.from_image_asset(
                directory=self.docker_file_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=[handler],
            ),
            timeout=Duration.minutes(3),
            memory_size=256,
            role=self.lambda_role,
        )
        lambda_fn.add_event_source(aws_lambda_event_sources.SqsEventSource(queue))
        error_alarm = aws_cloudwatch.Alarm(
            self,
            f"{engine_name}LambdaErrors",
            alarm_name=f"{engine_name}LambdaErrors",
            metric=lambda_fn.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=aws_cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Alarm when {engine_name} lambda has errors",
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )
        error_alarm.add_alarm_action(aws_cloudwatch_actions.SnsAction(self.alarm_topic))
