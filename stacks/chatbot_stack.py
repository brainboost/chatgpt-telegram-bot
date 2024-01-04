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
    aws_s3,
    aws_sns,
    aws_sqs,
    aws_ssm,
    triggers,
)
from aws_cdk import aws_lambda as _lambda
from aws_cdk.aws_lambda import Code, DockerImageCode, DockerImageFunction
from constructs import Construct

LAMBDA_ASSET_PATH = "lambda"


class ChatBotStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, stage: str, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambda_role = aws_iam.Role(
            self,
            "ChatBotRole",
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

        lambda_role.add_to_policy(
            aws_iam.PolicyStatement(
                actions=[
                    "sns:Publish",
                    "sns:ReceiveMessage",
                    "sqs:ReceiveMessage",
                    "sqs:ListQueues",
                    "sqs:SendMessage",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes",
                    "sqs:StartMessageMoveTask",
                    "sqs:ListMessageMoveTasks",
                ],
                resources=["*"],
            )
        )

        bucket = aws_s3.Bucket(
            self,
            f"{construct_id}-s3-Bucket",
            bucket_name=f"{construct_id}-s3-bucket-{stage}-tmp".lower(),
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=aws_s3.BlockPublicAccess.BLOCK_ALL,
            encryption=aws_s3.BucketEncryption.S3_MANAGED,
            versioned=True,
        )
        bucket.add_lifecycle_rule(id="expiration-rule",
            object_size_greater_than=1024 * 100,
            noncurrent_version_expiration=Duration.days(15),
            enabled=True,
        )
        result_dlq = aws_sqs.Queue(
            self,
            "Result-Queue-DLQ",
            queue_name="Result-Queue-DLQ",
            removal_policy=RemovalPolicy.DESTROY,
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(5),
            enforce_ssl=True,
        )

        docker_path = str(Path(__file__).parent.parent.resolve())
        result_handler = DockerImageFunction(
            self,
            "ResultProcessingHandler",
            function_name="ResultProcessingHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=[f"{LAMBDA_ASSET_PATH}.results.response_handler"],
            ),
            timeout=Duration.minutes(1),
            role=lambda_role,
            log_retention=aws_logs.RetentionDays.TWO_WEEKS,
            dead_letter_queue_enabled=True,
            dead_letter_queue=result_dlq,
        )
        self.result_topic = aws_sns.Topic(
            self,
            "ResultTopic",
            display_name="Result SNS topic",
            topic_name="result-ai-topic",
        )
        result_handler.add_event_source(
            aws_lambda_event_sources.SnsEventSource(
                topic=self.result_topic,
            )
        )
        aws_ssm.StringParameter(
            self,
            "snsResultTopicParam",
            parameter_name="RESULT_SNS_TOPIC_ARN",
            string_value=self.result_topic.topic_arn,
        )

        lambda_function = DockerImageFunction(
            self,
            "BotHandler",
            function_name="BotHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=[f"{LAMBDA_ASSET_PATH}.chatbot.telegram_api_handler"],
            ),
            timeout=Duration.minutes(1),
            role=lambda_role,
            dead_letter_queue=aws_sqs.Queue(
                self,
                "BotHandler-DLQ",
                queue_name="BotHandler-DLQ",
                retention_period=Duration.days(5),
            ),
            log_retention=aws_logs.RetentionDays.TWO_WEEKS,
        )

        lambda_url = lambda_function.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE,
            cors={
                "allowed_origins": ["https://*"],
                "allowed_methods": [_lambda.HttpMethod.POST],
            },
        )

        lambda_url_param = aws_ssm.StringParameter(
            self,
            "LambdaFunctionURLParam",
            parameter_name="BOT_LAMBDA_URL",
            string_value=lambda_url.url,
        )

        aws_ssm.StringParameter(
            self,
            "s3BucketNameParam",
            parameter_name="BOT_S3_BUCKET",
            string_value=bucket.bucket_name,
        )

        triggers.TriggerFunction(
            self,
            "WebhookTriggerHandler",
            function_name="WebhookTriggerHandler",
            runtime=_lambda.Runtime.FROM_IMAGE,
            handler=_lambda.Handler.FROM_IMAGE,
            execute_after=[lambda_url_param],
            timeout=Duration.minutes(1),
            code=Code.from_asset_image(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                cmd=[f"{LAMBDA_ASSET_PATH}.webhook.lambda_handler"],
            ),
            role=lambda_role,
            execute_on_handler_change=True,
            log_retention=aws_logs.RetentionDays.TWO_WEEKS,
        )

        # Alarms

        alarm_topic = aws_sns.Topic(
            self,
            "ErrorsAlarms",
            topic_name="ErrorsAlarms",
            display_name="Errors Alarms Topic",
        )

        notify_email = aws_ssm.StringParameter.value_for_string_parameter(
            self, "ALARM_EMAIL"
        )

        aws_sns.Subscription(
            self,
            "AlarmEmailSubscription",
            topic=alarm_topic,
            endpoint=notify_email,
            protocol=aws_sns.SubscriptionProtocol.EMAIL,
        )

        result_dlq_alarm = aws_cloudwatch.Alarm(
            self,
            "ResultDlqAlarm",
            alarm_name="ResultDlqAlarm",
            alarm_description="Alarm when Result DLQ queue has messages",
            metric=result_dlq.metric_approximate_number_of_messages_visible(),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        )

        result_dlq_alarm.add_alarm_action(aws_cloudwatch_actions.SnsAction(alarm_topic))

        bot_handler_error_alarm = aws_cloudwatch.Alarm(
            self,
            "BotHandlerLambdaErrors",
            alarm_name="BotHandlerLambdaErrors",
            metric=lambda_function.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=aws_cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Alarm when BotHandler lambda has errors",
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )

        bot_handler_error_alarm.add_alarm_action(
            aws_cloudwatch_actions.SnsAction(alarm_topic)
        )
