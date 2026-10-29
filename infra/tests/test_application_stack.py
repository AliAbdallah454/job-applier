"""Storage boundaries for the independently deployed Application stack."""

import unittest

from aws_cdk import App
from aws_cdk.assertions import Match, Template

from infra.application_stack import ApplicationStack


class ApplicationStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = Template.from_stack(ApplicationStack(App(), "TestApplication"))

    def test_profile_table_is_retained_on_demand_and_keyed_by_user(self):
        self.template.resource_count_is("AWS::DynamoDB::Table", 2)
        self.template.has_resource(
            "AWS::DynamoDB::Table",
            {
                "DeletionPolicy": "Retain",
                "UpdateReplacePolicy": "Retain",
                "Properties": Match.object_like(
                    {
                        "BillingMode": "PAY_PER_REQUEST",
                        "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
                        "AttributeDefinitions": [
                            {"AttributeName": "user_id", "AttributeType": "S"}
                        ],
                        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
                        "SSESpecification": {"SSEEnabled": True},
                    }
                ),
            },
        )

    def test_documents_bucket_is_private_versioned_encrypted_and_retained(self):
        self.template.resource_count_is("AWS::S3::Bucket", 1)
        self.template.has_resource(
            "AWS::S3::Bucket",
            {
                "DeletionPolicy": "Retain",
                "UpdateReplacePolicy": "Retain",
                "Properties": {
                    "BucketEncryption": {
                        "ServerSideEncryptionConfiguration": [
                            {
                                "ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                            }
                        ]
                    },
                    "PublicAccessBlockConfiguration": {
                        "BlockPublicAcls": True,
                        "BlockPublicPolicy": True,
                        "IgnorePublicAcls": True,
                        "RestrictPublicBuckets": True,
                    },
                    "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
                    "VersioningConfiguration": {"Status": "Enabled"},
                },
            },
        )
        self.template.has_resource_properties(
            "AWS::S3::BucketPolicy",
            {
                "PolicyDocument": Match.object_like(
                    {
                        "Statement": Match.array_with(
                            [
                                Match.object_like(
                                    {
                                        "Effect": "Deny",
                                        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                                    }
                                ),
                            ]
                        )
                    }
                ),
            },
        )

    def test_generation_queue_retries_and_dead_letters(self):
        self.template.resource_count_is("AWS::SQS::Queue", 2)
        self.template.has_resource_properties(
            "AWS::SQS::Queue",
            {
                "VisibilityTimeout": 600,
                "MessageRetentionPeriod": 345600,
                "SqsManagedSseEnabled": True,
                "RedrivePolicy": {"deadLetterTargetArn": Match.any_value(), "maxReceiveCount": 5},
            },
        )
        self.template.has_resource_properties(
            "AWS::SQS::Queue", {"MessageRetentionPeriod": 1209600}
        )

    def test_generation_table_links_user_and_job(self):
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "generation_id", "KeyType": "RANGE"},
                ],
                "GlobalSecondaryIndexes": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "IndexName": "ByUserJob",
                                "KeySchema": [
                                    {"AttributeName": "user_id", "KeyType": "HASH"},
                                    {"AttributeName": "job_created", "KeyType": "RANGE"},
                                ],
                            }
                        )
                    ]
                ),
            },
        )

    def test_worker_scales_from_zero_with_bounded_concurrency(self):
        self.template.has_resource_properties(
            "AWS::ECS::Service",
            {
                "DesiredCount": 0,
                "LaunchType": "FARGATE",
            },
        )
        self.template.has_resource_properties(
            "AWS::ApplicationAutoScaling::ScalableTarget",
            {
                "MinCapacity": 0,
                "MaxCapacity": 2,
            },
        )
        self.template.resource_count_is("AWS::EC2::NatGateway", 0)
        self.template.resource_count_is("AWS::EC2::SecurityGroupIngress", 0)
        tasks = self.template.find_resources("AWS::ECS::TaskDefinition")
        container = next(iter(tasks.values()))["Properties"]["ContainerDefinitions"][0]
        self.assertEqual(container["User"], "10001:10001")
        environment = {item["Name"]: item["Value"] for item in container["Environment"]}
        self.assertEqual(environment["DOCUMENT_TIMEOUT_SECONDS"], "600")
        self.assertEqual(environment["DOCUMENT_MODEL_ID"], "us.amazon.nova-pro-v1:0")

    def test_worker_permissions_do_not_include_profile_or_jobs_tables(self):
        resources = self.template.to_json()["Resources"]
        policies = [
            value["Properties"]["PolicyDocument"]["Statement"]
            for name, value in resources.items()
            if value["Type"] == "AWS::IAM::Policy" and "DocumentTaskTaskRole" in name
        ]
        self.assertEqual(len(policies), 1)
        statements = policies[0]
        serialized = str(statements)
        self.assertNotIn("Profiles", serialized)
        self.assertNotIn("JobsTable", serialized)
        self.assertNotIn("dynamodb:Scan", serialized)
        self.assertNotIn("dynamodb:PutItem", serialized)
        invoke = [
            statement
            for statement in statements
            if "bedrock:InvokeModel" in statement.get("Action", [])
        ]
        self.assertEqual(len(invoke), 2)
        foundation = next(statement for statement in invoke if "Condition" in statement)
        self.assertIn("bedrock:InferenceProfileArn", foundation["Condition"]["StringEquals"])
        self.assertIn("foundation-model/amazon.nova-pro-v1:0", str(foundation["Resource"]))

    def test_document_repository_is_separate_from_pipeline_images(self):
        self.template.resource_count_is("AWS::ECR::Repository", 1)
        self.template.has_resource_properties(
            "AWS::ECR::Repository",
            {
                "RepositoryName": "testapplication/documents",
                "ImageTagMutability": "IMMUTABLE",
            },
        )
        self.template.resource_count_is("Custom::WorkerImage", 0)
        self.template.resource_count_is("AWS::CodeBuild::Project", 0)

    def test_outputs(self):
        for name in [
            "ProfilesTable",
            "DocumentsBucketName",
            "DocumentGenerationsTable",
            "DocumentQueueUrl",
            "DocumentDLQUrl",
            "DocumentModelId",
            "DocumentClusterName",
            "DocumentWorkerService",
            "DocumentWorkerLogGroup",
        ]:
            self.template.has_output(name, {"Value": Match.any_value()})
        self.template.resource_count_is("AWS::Cognito::UserPool", 0)

    def test_worker_context_overrides(self):
        template = Template.from_stack(
            ApplicationStack(
                App(
                    context={
                        "maxDocumentWorkers": 3,
                        "documentTimeoutSeconds": 900,
                        "documentModelId": "anthropic.claude-sonnet-4-6",
                    }
                ),
                "ConfiguredApplication",
            )
        )
        template.has_resource_properties(
            "AWS::ApplicationAutoScaling::ScalableTarget",
            {
                "MinCapacity": 0,
                "MaxCapacity": 3,
            },
        )
        statements = [
            statement
            for policy in template.find_resources("AWS::IAM::Policy").values()
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]
            if "bedrock:InvokeModel" in statement.get("Action", [])
        ]
        self.assertEqual(len(statements), 1)
        self.assertNotIn("Condition", statements[0])
        self.assertIn(
            "foundation-model/anthropic.claude-sonnet-4-6", str(statements[0]["Resource"])
        )

    def test_invalid_worker_context_is_rejected(self):
        for context in [
            {"maxDocumentWorkers": 0},
            {"documentTimeoutSeconds": 1},
            {"documentModelId": "*"},
        ]:
            with self.subTest(context=context), self.assertRaises(ValueError):
                ApplicationStack(App(context=context), "InvalidApplication")
