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
        self.template.resource_count_is("AWS::DynamoDB::Table", 1)
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

    def test_outputs_and_no_compute_or_auth_resources(self):
        self.template.has_output("ProfilesTable", {"Value": Match.any_value()})
        self.template.has_output("DocumentsBucketName", {"Value": Match.any_value()})
        resources = self.template.to_json()["Resources"]
        self.assertEqual(
            {resource["Type"] for resource in resources.values()},
            {"AWS::DynamoDB::Table", "AWS::S3::Bucket", "AWS::S3::BucketPolicy"},
        )
