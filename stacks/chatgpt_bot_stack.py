from aws_cdk import (
    Duration,
    Stack,
    CfnOutput,
    aws_lambda_python_alpha as _pylambda,
    RemovalPolicy as _removalpolicy,
    aws_s3 as _s3,
    aws_lambda as _lambda,
    aws_iam as iam
)
from constructs import Construct
from aws_cdk.aws_lambda_python_alpha import PythonLayerVersion

LAMBDA_ASSET_PATH = "lambda"

class ChatgptBotStack(Stack):
    bot_lambda: _pylambda.PythonFunction

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambda_role = iam.Role(self, "BotRole",
                               assumed_by=iam.ServicePrincipal(
                                   "lambda.amazonaws.com"),
                               managed_policies=[
                                   iam.ManagedPolicy.from_aws_managed_policy_name(
                                       "service-role/AWSLambdaBasicExecutionRole"
                                   )
                               ],
                               )

        lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMFullAccess")
        )

        self.bucket = _s3.Bucket(self, "VoiceMessagesBucket",
                            bucket_name="chatgpt-squash-voice-messages-bucket",
                            auto_delete_objects=True,
                            removal_policy=_removalpolicy.DESTROY,
                            block_public_access=_s3.BlockPublicAccess.BLOCK_ALL,
                            versioned=False)

        self.lambda_layer = PythonLayerVersion(self, "CommonLayer", 
            entry=LAMBDA_ASSET_PATH, 
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_9])

        self.bot_lambda = _lambda.Function(self, "BotHandler",
                                         runtime=_lambda.Runtime.PYTHON_3_9,
                                         code=_lambda.Code.from_asset(LAMBDA_ASSET_PATH),
                                         handler="chatbot.message_handler",
                                         layers=[self.lambda_layer],
                                         timeout=Duration.minutes(5),
                                         role=lambda_role)

        lambdaUrl = self.bot_lambda.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE, cors=None)

        CfnOutput(scope=self, id="chatbotLambdaUrl",
                  value=lambdaUrl.url, export_name="chatbotLambdaUrl")
        CfnOutput(scope=self, id="voiceMessagesBucketName",
                  value=self.bucket.bucket_name, export_name="voiceMessagesBucketName")
