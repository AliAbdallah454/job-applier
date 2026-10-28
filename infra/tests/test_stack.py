"""CloudFormation contract tests; no AWS account or Docker daemon needed."""

import json
import unittest
from pathlib import Path

from aws_cdk import App
from aws_cdk.assertions import Match, Template

from infra.stack import JobPipelineStack


class StackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = App()
        cls.stack = JobPipelineStack(cls.app, "TestPipeline")
        cls.template = Template.from_stack(cls.stack)

    def test_job_source_bucket_is_private_encrypted_and_retained(self):
        self.template.resource_count_is("AWS::S3::Bucket", 1)
        self.template.has_resource(
            "AWS::S3::Bucket",
            {
                "DeletionPolicy": "Retain",
                "UpdateReplacePolicy": "Retain",
                "Properties": Match.object_like(
                    {
                        "BucketEncryption": {
                            "ServerSideEncryptionConfiguration": [
                                {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                            ]
                        },
                        "PublicAccessBlockConfiguration": {
                            "BlockPublicAcls": True,
                            "BlockPublicPolicy": True,
                            "IgnorePublicAcls": True,
                            "RestrictPublicBuckets": True,
                        },
                        "OwnershipControls": {
                            "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
                        },
                        "VersioningConfiguration": {"Status": "Enabled"},
                    }
                ),
            },
        )
        self.template.has_resource_properties(
            "AWS::S3::BucketPolicy",
            {
                "PolicyDocument": {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Effect": "Deny",
                                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                                }
                            )
                        ]
                    ),
                    "Version": "2012-10-17",
                }
            },
        )
        self.template.has_output("JobSourceBucketName", {"Value": Match.any_value()})

    def test_two_retained_on_demand_tables_and_filter_indexes(self):
        self.template.resource_count_is("AWS::DynamoDB::Table", 2)
        self.template.has_resource(
            "AWS::DynamoDB::Table",
            {
                "DeletionPolicy": "Retain",
                "Properties": Match.object_like(
                    {
                        "BillingMode": "PAY_PER_REQUEST",
                        "StreamSpecification": {"StreamViewType": "NEW_AND_OLD_IMAGES"},
                    }
                ),
            },
        )
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "GlobalSecondaryIndexes": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "IndexName": "ByCategorySeniority",
                                "KeySchema": [
                                    {"AttributeName": "category", "KeyType": "HASH"},
                                    {"AttributeName": "seniority_job", "KeyType": "RANGE"},
                                ],
                            }
                        ),
                        Match.object_like({"IndexName": "ByTechSeniority"}),
                    ]
                ),
            },
        )

    def test_worker_services_start_at_zero_and_have_scaling_limits(self):
        self.template.resource_count_is("AWS::ECS::Service", 2)
        for service in self.template.find_resources("AWS::ECS::Service").values():
            props = service["Properties"]
            self.assertEqual(props["DesiredCount"], 0)
            self.assertEqual(props["LaunchType"], "FARGATE")
            self.assertEqual(
                props["NetworkConfiguration"]["AwsvpcConfiguration"]["AssignPublicIp"], "ENABLED"
            )
        limits = {
            resource["Properties"]["MaxCapacity"]
            for resource in self.template.find_resources(
                "AWS::ApplicationAutoScaling::ScalableTarget"
            ).values()
        }
        self.assertEqual(limits, {2, 4})
        self.template.resource_count_is("AWS::EC2::NatGateway", 0)
        for resource in self.template.find_resources("AWS::EC2::SecurityGroup").values():
            self.assertFalse(resource["Properties"].get("SecurityGroupIngress"))

    def test_each_task_uses_a_separate_docker_image(self):
        containers = [
            task["Properties"]["ContainerDefinitions"][0]
            for task in self.template.find_resources("AWS::ECS::TaskDefinition").values()
        ]
        self.assertEqual(len(containers), 3)
        self.assertEqual(len({json.dumps(c["Image"], sort_keys=True) for c in containers}), 3)
        self.assertEqual(
            {tuple(c["Command"]) for c in containers},
            {
                ("-m", "backend.jobs.workers.seed"),
                ("-m", "backend.jobs.workers.scraper"),
                ("-m", "backend.jobs.workers.classifier"),
            },
        )

        dockerfiles = set()
        for manifest_path in Path(self.app.synth().directory).glob("*.assets.json"):
            manifest = json.loads(manifest_path.read_text())
            dockerfiles.update(
                image["source"]["dockerFile"] for image in manifest.get("dockerImages", {}).values()
            )
        self.assertEqual(
            dockerfiles,
            {
                "containers/Dockerfile.seed",
                "containers/Dockerfile.scrape",
                "containers/Dockerfile.classify",
            },
        )

    def test_key_is_empty_not_embedded_and_only_classifier_gets_it(self):
        self.template.resource_count_is("AWS::SecretsManager::Secret", 1)
        secret = next(iter(self.template.find_resources("AWS::SecretsManager::Secret").values()))
        self.assertNotIn("SecretString", secret["Properties"])
        self.assertNotIn("GenerateSecretString", secret["Properties"])
        secret_consumers = []
        for task in self.template.find_resources("AWS::ECS::TaskDefinition").values():
            container = task["Properties"]["ContainerDefinitions"][0]
            self.assertTrue(container["ReadonlyRootFilesystem"])
            for binding in container.get("Secrets", []):
                if binding["Name"] == "TYPESAFE_API_KEY":
                    secret_consumers.append(container["Command"])
        self.assertEqual(secret_consumers, [["-m", "backend.jobs.workers.classifier"]])
        self.template.resource_count_is("AWS::SSM::Parameter", 3)

    def test_stream_retries_partial_failures_and_queues_have_dead_letters(self):
        self.template.has_resource_properties(
            "AWS::Lambda::EventSourceMapping",
            {
                "StartingPosition": "TRIM_HORIZON",
                "FunctionResponseTypes": ["ReportBatchItemFailures"],
                "MaximumRetryAttempts": 3,
                "DestinationConfig": {"OnFailure": {"Destination": Match.any_value()}},
            },
        )
        queues = self.template.find_resources("AWS::SQS::Queue")
        workers = [q["Properties"] for q in queues.values() if "RedrivePolicy" in q["Properties"]]
        self.assertEqual(len(workers), 2)
        for queue in workers:
            self.assertEqual(queue["VisibilityTimeout"], 600)
            self.assertEqual(queue["RedrivePolicy"]["maxReceiveCount"], 5)

    def test_schedule_opt_in_and_separate_task_roles(self):
        self.template.has_resource_properties("AWS::Events::Rule", {"State": "DISABLED"})
        roles = {
            json.dumps(task["Properties"]["TaskRoleArn"], sort_keys=True)
            for task in self.template.find_resources("AWS::ECS::TaskDefinition").values()
        }
        self.assertEqual(len(roles), 3)

    def test_collection_defaults_are_unlimited(self):
        parameters = self.template.find_resources("AWS::SSM::Parameter")
        limits = {
            name: resource["Properties"]["Value"]
            for name, resource in parameters.items()
            if name.startswith(("MaxBoards", "JobsPerBoard"))
        }
        self.assertEqual(len(limits), 2)
        self.assertEqual(set(limits.values()), {"all"})

    def test_parallelism_and_schedule_context(self):
        app = App(
            context={
                "maxScrapers": "8",
                "maxClassifiers": "3",
                "schedule": "true",
            }
        )
        template = Template.from_stack(JobPipelineStack(app, "ConfiguredPipeline"))
        template.resource_count_is("AWS::SecretsManager::Secret", 1)
        template.has_resource_properties("AWS::Events::Rule", {"State": "ENABLED"})
        template.has_resource_properties(
            "AWS::ApplicationAutoScaling::ScalableTarget", {"MaxCapacity": 8, "MinCapacity": 0}
        )

    def test_scraper_cannot_read_secret_or_write_results(self):
        policies = self.template.find_resources("AWS::IAM::Policy")
        scraper_policy = [
            p["Properties"]["PolicyDocument"]
            for key, p in policies.items()
            if "ScraperTaskTaskRole" in key
        ]
        self.assertEqual(len(scraper_policy), 1)
        policy = json.dumps(scraper_policy[0])
        self.assertNotIn("secretsmanager", policy)
        self.assertNotIn("Classifications", policy)
        self.assertIn("dynamodb:UpdateItem", policy)
        classifier_policy = [
            p["Properties"]["PolicyDocument"]
            for key, p in policies.items()
            if "ClassifierTaskTaskRole" in key
        ]
        self.assertIn("dynamodb:ConditionCheckItem", json.dumps(classifier_policy))

    def test_invalid_cost_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            JobPipelineStack(App(context={"jobsPerBoard": "0"}), "InvalidPipeline")
