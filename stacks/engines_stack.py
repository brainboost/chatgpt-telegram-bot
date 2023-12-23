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
        self.docker_file_path = str(Path(__file__).parent.parent.resolve())

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

        # AI Engine Lambdas

        # Bing

        self.__create_engine(
            engine_name="Bing",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["text", "command"]
                ),
                "engines": aws_sns.SubscriptionFilter.string_filter(allowlist=["bing"]),
            },
            handler=f"{ASSET_PATH}.bing.sns_handler",
        )

        # Bard

        self.__create_engine(
            engine_name="Bard",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["text", "command"]
                ),
                "engines": aws_sns.SubscriptionFilter.string_filter(allowlist=["bard"]),
            },
            handler=f"{ASSET_PATH}.bard.sns_handler",
        )

        # ChatGPT

        self.__create_engine(
            engine_name="ChatGpt",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["text", "command"]
                ),
                "engines": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["chatgpt"]
                ),
            },
            handler=f"{ASSET_PATH}.chat_gpt.sns_handler",
        )

        # Dall-E

        self.__create_engine(
            engine_name="Dalle",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(allowlist=["imagine"]),
            },
            handler=f"{ASSET_PATH}.dalle_img.sns_handler",
        )

        # DeepL

        self.__create_engine(
            engine_name="DeepL",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["translate"]
                ),
            },
            handler=f"{ASSET_PATH}.deepl_tr.sns_handler",
        )

        # LLama2

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
            handler=f"{ASSET_PATH}.monsterapi.sns_handler",
        )

        # Add monsterapi callback handler
        dlq = aws_sqs.Queue(
            self,
            "MonsterApi-Callback-DLQ",
            queue_name="MonsterApi-Callback-DLQ",
            removal_policy=RemovalPolicy.DESTROY,
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(5),
            enforce_ssl=True,
        )
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
            dead_letter_queue_enabled=True,
            dead_letter_queue=dlq,
            log_retention=aws_logs.RetentionDays.TWO_WEEKS,
        )
        dlq.grant_send_messages(api_callback)
        error_alarm = aws_cloudwatch.Alarm(
            self,
            "MonsterApiCallbackDlqErrors",
            alarm_name="MonsterApiCallbackDlqErrors",
            alarm_description="Alarm when MonsterApi callback DLQ has messages",
            metric=dlq.metric_approximate_number_of_messages_visible(),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
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
            handler=f"{ASSET_PATH}.ideogram_img.sns_handler",
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
                    retention_period=Duration.days(5),
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
            log_retention=aws_logs.RetentionDays.TWO_WEEKS,
            role=self.lambda_role,
        )
        resultHandler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(resultQueue)
        )

        # Claude

        self.__create_engine(
            engine_name="Claude",
            sns_filter_policy={
                "type": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["text", "command"]
                ),
                "engines": aws_sns.SubscriptionFilter.string_filter(
                    allowlist=["claude"]
                ),
            },
            handler=f"{ASSET_PATH}.claude.sns_handler",
        )

    def __create_engine(
        self,
        engine_name: str,
        sns_filter_policy: any,
        handler: str,
    ) -> None:
        """Creates infrastructure for the AI engine handler (queue-lambda-alarm)."""

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
            timeout=Duration.minutes(5),
            memory_size=256,
            log_retention=aws_logs.RetentionDays.TWO_WEEKS,
            role=self.lambda_role,
            dead_letter_queue_enabled=True,
            dead_letter_queue=self.dlq,
        )
        lambda_fn.add_event_source(
            aws_lambda_event_sources.SnsEventSource(
                topic=self.request_topic,
                filter_policy=sns_filter_policy,
                # dead_letter_queue=self.dlq,
            )
        )
