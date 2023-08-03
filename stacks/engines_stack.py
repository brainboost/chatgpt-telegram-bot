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

        alarm_topic = aws_sns.Topic(
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
            topic=alarm_topic,
            endpoint=notify_email,
            protocol=aws_sns.SubscriptionProtocol.EMAIL,
        )

        request_dlq_alarm = aws_cloudwatch.Alarm(
            self,
            "RequestDlqAlarm",
            alarm_name="RequestDlqAlarm",
            alarm_description="Alarm when Request DLQ queue has messages",
            metric=request_dlq.metric_approximate_number_of_messages_visible(),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        )
        request_dlq_alarm.add_alarm_action(
            aws_cloudwatch_actions.SnsAction(alarm_topic)
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

        docker_path = str(Path(__file__).parent.parent.resolve())
        bing_handler = DockerImageFunction(
            self,
            "BingHandler",
            function_name="BingHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=[f"{ASSET_PATH}.bing_gpt.sqs_handler"],
            ),
            timeout=Duration.minutes(3),
            memory_size=256,
            role=lambda_role,
        )
        bing_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(bing_queue)
        )

        bing_error_alarm = aws_cloudwatch.Alarm(
            self,
            "BingLambdaErrors",
            alarm_name="BingLambdaErrors",
            metric=bing_handler.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=aws_cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Alarm when Bing lambda has errors",
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )
        bing_error_alarm.add_alarm_action(aws_cloudwatch_actions.SnsAction(alarm_topic))

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
                cmd=[f"{ASSET_PATH}.bard_engine.sqs_handler"],
            ),
            timeout=Duration.minutes(3),
            role=lambda_role,
        )

        bard_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(bard_queue)
        )

        bard_error_alarm = aws_cloudwatch.Alarm(
            self,
            "BardLambdaErrors",
            alarm_name="BardLambdaErrors",
            metric=bard_handler.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=aws_cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Alarm when Google Bard lambda has errors",
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )
        bard_error_alarm.add_alarm_action(aws_cloudwatch_actions.SnsAction(alarm_topic))

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
                cmd=[f"{ASSET_PATH}.chat_gpt.sqs_handler"],
            ),
            timeout=Duration.minutes(3),
            memory_size=256,
            role=lambda_role,
        )
        chatgpt_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(chat_queue)
        )

        chatgpt_error_alarm = aws_cloudwatch.Alarm(
            self,
            "ChatGptLambdaErrors",
            alarm_name="ChatGptLambdaErrors",
            metric=chatgpt_handler.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=aws_cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Alarm when ChatGPT lambda has errors",
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )
        chatgpt_error_alarm.add_alarm_action(
            aws_cloudwatch_actions.SnsAction(alarm_topic)
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
                cmd=[f"{ASSET_PATH}.dalle_img.sqs_handler"],
            ),
            timeout=Duration.minutes(3),
            role=lambda_role,
        )
        dalle_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(dalee_queue)
        )

        dalle_error_alarm = aws_cloudwatch.Alarm(
            self,
            "DalleLambdaErrors",
            alarm_name="DalleLambdaErrors",
            metric=dalle_handler.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=aws_cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Alarm when Dall-E lambda has errors",
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )
        dalle_error_alarm.add_alarm_action(
            aws_cloudwatch_actions.SnsAction(alarm_topic)
        )

        # DeepL

        deepl_queue = aws_sqs.Queue(
            self,
            "Deepl-Request-Queue",
            queue_name="Deepl-Request-Queue",
            removal_policy=RemovalPolicy.DESTROY,
            visibility_timeout=Duration.minutes(3),
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=self.dlq,
            enforce_ssl=True,
        )
        request_topic.add_subscription(
            aws_sns_subscriptions.SqsSubscription(
                queue=deepl_queue,
                raw_message_delivery=True,
                filter_policy={
                    "type": aws_sns.SubscriptionFilter.string_filter(
                        allowlist=["translate"]
                    ),
                },
            )
        )
        deepl_handler = DockerImageFunction(
            self,
            "DeeplHandler",
            function_name="DeeplHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=[f"{ASSET_PATH}.deepl_tr.sqs_handler"],
            ),
            timeout=Duration.minutes(3),
            role=lambda_role,
        )
        deepl_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(deepl_queue)
        )

        deepl_error_alarm = aws_cloudwatch.Alarm(
            self,
            "DeepLLambdaErrors",
            alarm_name="DeepLLambdaErrors",
            metric=deepl_handler.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=aws_cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Alarm when DeepL lambda has errors",
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )
        deepl_error_alarm.add_alarm_action(
            aws_cloudwatch_actions.SnsAction(alarm_topic)
        )
