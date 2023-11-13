from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


class DatabaseStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        dynamodb.Table(
            self,
            "user-configurations-table",
            table_name="user-configurations",
            partition_key=dynamodb.Attribute(
                name="user_id", type=dynamodb.AttributeType.NUMBER
            ),
            removal_policy=RemovalPolicy.RETAIN,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        new_conversationTable = dynamodb.Table(
            self,
            "user-conversations-table",
            table_name="user-conversations",
            partition_key=dynamodb.Attribute(
                name="conversation_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="request_id", type=dynamodb.AttributeType.STRING
            ),
            removal_policy=RemovalPolicy.RETAIN,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        new_conversationTable.add_global_secondary_index(
            index_name="userid-index",
            partition_key=dynamodb.Attribute(
                name="user_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="engine", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        new_conversationTable.add_local_secondary_index(
            index_name="timestamp-index",
            sort_key=dynamodb.Attribute(
                name="timestamp", type=dynamodb.AttributeType.NUMBER
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        contextTable = dynamodb.Table(
            self,
            "user-context-table",
            table_name="user-context",
            partition_key=dynamodb.Attribute(
                name="user_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="engine", type=dynamodb.AttributeType.STRING
            ),
            removal_policy=RemovalPolicy.RETAIN,
            time_to_live_attribute="exp",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )
        contextTable.add_global_secondary_index(
            index_name="conversation-id-index",
            partition_key=dynamodb.Attribute(
                name="conversation_id", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        dynamodb.Table(
            self,
            "request-jobs-table",
            table_name="request-jobs",
            partition_key=dynamodb.Attribute(
                name="request_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="engine", type=dynamodb.AttributeType.STRING
            ),
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="exp",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )
