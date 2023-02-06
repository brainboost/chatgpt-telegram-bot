from aws_cdk import (
    Stack,
    Duration,
    Fn,
    aws_lambda as _lambda,
    aws_lambda_python_alpha as _pylambda,
    triggers,
    aws_ssm as _ssm,
    aws_iam as iam
)
from constructs import Construct

LAMBDA_ASSET_PATH = "lambda"


class WebhookStack(Stack):
    webhook_lambda: _pylambda.PythonFunction

    def __init__(self, scope: Construct, construct_id: str, lambda_layer, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        botLambdaUrl = Fn.import_value("chatbotLambdaUrl")
        bucketName = Fn.import_value("voiceMessagesBucketName")

        webhook_lambda_role = iam.Role(self, "WebhookLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        webhook_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMFullAccess")
        )

        lambda_url_param = _ssm.StringParameter(
            self, "lambdaUrl", parameter_name="LAMBDA_URL", string_value=botLambdaUrl)
        _ssm.StringParameter(
            self, "s3BucketName", parameter_name="VOICE_MESSAGES_BUCKET", string_value=bucketName)

        self.webhook_trigger = triggers.TriggerFunction(self, "WebhookTrigger",
                                                        runtime=_lambda.Runtime.PYTHON_3_9,
                                                        handler="webhook.lambda_handler",
                                                        layers=[lambda_layer],
                                                        execute_after=[lambda_url_param],
                                                        timeout=Duration.minutes(1),
                                                        code=_lambda.Code.from_asset(LAMBDA_ASSET_PATH),
                                                        role=webhook_lambda_role,
                                                        execute_on_handler_change=False
                                                        )
