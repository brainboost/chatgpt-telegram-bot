from pathlib import Path

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudwatch,
    aws_cloudwatch_actions,
    aws_ec2,
    aws_iam,
    aws_lambda_event_sources,
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

        vpc = aws_ec2.Vpc(
            self,
            "ChatBotVpc",
            ip_addresses=aws_ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            subnet_configuration=[
                aws_ec2.SubnetConfiguration(
                    subnet_type=aws_ec2.SubnetType.PRIVATE_ISOLATED,
                    name="private",
                    cidr_mask=24,
                ),
                aws_ec2.SubnetConfiguration(
                    subnet_type=aws_ec2.SubnetType.PUBLIC, name="public", cidr_mask=24
                ),
            ],
        )

        sg = aws_ec2.SecurityGroup(
            self,
            "ChatBotSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
            security_group_name="ChatBotSecurityGroup",
        )

        # Allow ingress from the specified TG IP ranges
        sg.add_ingress_rule(
            peer=aws_ec2.Peer.ipv4("149.154.160.0/20"),
            connection=aws_ec2.Port.tcp(443),
            description="Allow HTTPS from 149.154.160.0/20",
        )
        sg.add_ingress_rule(
            peer=aws_ec2.Peer.ipv4("91.108.4.0/22"),
            connection=aws_ec2.Port.tcp(443),
            description="Allow HTTPS from 91.108.4.0/22",
        )

        lambda_role = aws_iam.Role(
            self,
            "ChatBotRole",
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

        lambda_role.add_to_policy(
            aws_iam.PolicyStatement(
                actions=[
                    "sns:Publish",
                    "sqs:ReceiveMessage",
                    "ec2:CreateNetworkInterface",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DeleteNetworkInterface",
                    "ec2:AssignPrivateIpAddresses",
                    "ec2:UnassignPrivateIpAddresses",
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

        docker_path = str(Path(__file__).parent.parent.resolve())
        result_handler = DockerImageFunction(
            self,
            "ResultProcessingHandler",
            function_name="ResultProcessingHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                # working_directory=LAMBDA_ASSET_PATH,
                cmd=[f"{LAMBDA_ASSET_PATH}.results.response_handler"],
            ),
            timeout=Duration.minutes(1),
            role=lambda_role,
        )

        # result SQS queue

        result_dlq = aws_sqs.Queue(
            self,
            "Result-Queue-DLQ",
            queue_name="Result-Queue-DLQ",
            removal_policy=RemovalPolicy.DESTROY,
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(3),
            enforce_ssl=True,
        )
        self.dlq = aws_sqs.DeadLetterQueue(
            max_receive_count=1,
            queue=result_dlq,
        )

        result_queue = aws_sqs.Queue(
            self,
            "Result-Queue",
            queue_name="Result-Queue",
            retention_period=Duration.days(3),
            visibility_timeout=Duration.minutes(1),
            removal_policy=RemovalPolicy.DESTROY,
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            dead_letter_queue=self.dlq,
        )
        result_queue.add_to_resource_policy(
            aws_iam.PolicyStatement(
                actions=["sqs:SendMessage"],
                principals=[aws_iam.AnyPrincipal()],
                resources=[result_queue.queue_arn],
            )
        )
        result_handler.add_event_source(
            aws_lambda_event_sources.SqsEventSource(result_queue)
        )
        aws_ssm.StringParameter(
            self,
            "sqsResultsQueueParam",
            parameter_name="RESULTS_SQS_QUEUE_URL",
            string_value=result_queue.queue_url,
        )

        lambda_function = DockerImageFunction(
            self,
            "BotHandler",
            function_name="BotHandler",
            code=DockerImageCode.from_image_asset(
                directory=docker_path,
                file="Dockerfile",
                exclude=["cdk.out"],
                # working_directory=LAMBDA_ASSET_PATH,
                cmd=[f"{LAMBDA_ASSET_PATH}.chatbot.telegram_api_handler"],
            ),
            timeout=Duration.minutes(1),
            role=lambda_role,
            dead_letter_queue=aws_sqs.Queue(
                self,
                "BotHandler-DLQ",
                queue_name="BotHandler-DLQ",
                retention_period=Duration.days(3),
            ),
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
            execute_on_handler_change=False,
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

        result_handler_error_alarm = aws_cloudwatch.Alarm(
            self,
            "ResultProcessingHandlerLambdaErrors",
            alarm_name="ResultProcessingHandlerLambdaErrors",
            metric=result_handler.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=aws_cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Alarm when ResultProcessingHandler lambda has errors",
            comparison_operator=aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )

        bot_handler_error_alarm.add_alarm_action(
            aws_cloudwatch_actions.SnsAction(alarm_topic)
        )
        result_handler_error_alarm.add_alarm_action(
            aws_cloudwatch_actions.SnsAction(alarm_topic)
        )
