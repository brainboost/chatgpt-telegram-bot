from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


class DatabaseStack(Stack):

    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        dynamodb.Table(
            self, "user-configurations-table",
            table_name="user-configurations",
            partition_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.NUMBER
            ),
            removal_policy=RemovalPolicy.RETAIN,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST
        )
        
        dynamodb.Table(
            self, "conversations-table",
            table_name="conversations",
            partition_key=dynamodb.Attribute(
                name="conversation_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.NUMBER
            ),
            removal_policy=RemovalPolicy.RETAIN,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST
        )