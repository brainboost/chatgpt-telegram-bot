from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_ec2,
    aws_iam,
    aws_lambda_event_sources,
    aws_s3,
    aws_sqs,
    aws_ssm,
    triggers,
)
from aws_cdk import aws_lambda as _lambda
from aws_cdk.aws_lambda_python_alpha import PythonLayerVersion
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

        lambda_layer = PythonLayerVersion(
            self,
            "BotLambdaLayer",
            entry=LAMBDA_ASSET_PATH,
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_9],
        )

        result_handler = _lambda.Function(
            self,
            "ResultProcessingHandler",
            code=_lambda.Code.from_asset(LAMBDA_ASSET_PATH),
            handler="results.response_handler",
            runtime=_lambda.Runtime.PYTHON_3_9,
            layers=[lambda_layer],
            timeout=Duration.minutes(1),
            role=lambda_role,
            # vpc=vpc,
            # security_groups=[sg],
        )

        # result SQS queue
        result_queue = aws_sqs.Queue(
            self,
            "Result-Queue",
            queue_name="Result-Queue",
            retention_period=Duration.days(3),
            visibility_timeout=Duration.minutes(1),
            removal_policy=RemovalPolicy.DESTROY,
            encryption=aws_sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
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

        lambda_function = _lambda.Function(
            self,
            "BotHandler",
            code=_lambda.Code.from_asset(LAMBDA_ASSET_PATH),
            handler="chatbot.telegram_api_handler",
            runtime=_lambda.Runtime.PYTHON_3_9,
            layers=[lambda_layer],
            timeout=Duration.minutes(1),
            role=lambda_role,
            # vpc=vpc,
            # security_groups=[sg],
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
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="webhook.lambda_handler",
            layers=[lambda_layer],
            execute_after=[lambda_url_param],
            timeout=Duration.minutes(1),
            code=_lambda.Code.from_asset(LAMBDA_ASSET_PATH),
            role=lambda_role,
            execute_on_handler_change=False,
        )
