from aws_cdk import Duration, Stack, triggers
from aws_cdk import RemovalPolicy as _removalpolicy
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_ssm as ssm
from aws_cdk.aws_lambda_python_alpha import PythonLayerVersion
from constructs import Construct

LAMBDA_ASSET_PATH = "lambda"

class ChatgptBotStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambda_role = iam.Role(
            self,
            "BotRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonS3ReadOnlyAccess"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonDynamoDBFullAccess"),
            ],
        )

        self.bucket = s3.Bucket(
            self,
            f"{construct_id}-Bucket",
            bucket_name=f"{construct_id}-s3-bucket".lower(),
            removal_policy=_removalpolicy.DESTROY,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
        )

        self.lambda_layer = PythonLayerVersion(
            self,
            "CommonLayer",
            entry=LAMBDA_ASSET_PATH,
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_9],
        )

        lambda_function = _lambda.Function(            
          self,
            "BotHandler",
            code=_lambda.Code.from_asset(LAMBDA_ASSET_PATH),
            handler="chatbot.message_handler",
            runtime=_lambda.Runtime.PYTHON_3_9,
            layers=[self.lambda_layer],
            timeout=Duration.minutes(5),
            role=lambda_role,
        )

        lambda_url = lambda_function.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE, cors=None
        ).url
        
        lambda_url_param = ssm.StringParameter(self, 'LambdaFunctionURLParam',
          parameter_name='LAMBDA_URL',
          string_value=lambda_url
        )

        ssm.StringParameter(self, "s3BucketNameParam",
            parameter_name="CHATBOT_S3_BUCKET",
            string_value=self.bucket.bucket_name,
        )

        self.webhook_trigger = triggers.TriggerFunction(
            self,
            "WebhookTriggerHandler",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="webhook.lambda_handler",
            layers=[self.lambda_layer],
            execute_after=[lambda_url_param],
            timeout=Duration.minutes(1),
            code=_lambda.Code.from_asset(LAMBDA_ASSET_PATH),
            role=lambda_role,
            execute_on_handler_change=False,
        )
