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
        # Exercise explicit limits independently of the deployment defaults.
        cls.app = App(context={"maxScrapers": 4, "maxClassifiers": 2})
        cls.stack = JobPipelineStack(cls.app, "TestPipeline")
        cls.template = Template.from_stack(cls.stack)

    def test_workers_have_separate_stack_named_repositories(self):
        repositories = self.template.find_resources("AWS::ECR::Repository")
        self.assertEqual(
            {item["Properties"]["RepositoryName"] for item in repositories.values()},
            {"testpipeline/seed", "testpipeline/scraper", "testpipeline/classifier"},
        )
        for resource_type in ["Custom::WorkerImage", "AWS::CodeBuild::Project"]:
            self.template.resource_count_is(resource_type, 0)

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
        for task in self.template.find_resources("AWS::ECS::TaskDefinition").values():
            container = task["Properties"]["ContainerDefinitions"][0]
            environment = {item["Name"]: item["Value"] for item in container["Environment"]}
            if container["Command"] == ["-m", "backend.jobs.workers.scraper"]:
                self.assertEqual(environment["SCRAPE_TIMEOUT_SECONDS"], "900")
            else:
                self.assertNotIn("SCRAPE_TIMEOUT_SECONDS", environment)
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

        manifests = Path(self.app.synth().directory).glob("*.assets.json")
        for manifest_path in manifests:
            self.assertFalse(json.loads(manifest_path.read_text()).get("dockerImages"))

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
        self.template.resource_count_is("AWS::SSM::Parameter", 1)

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

    def test_no_scheduled_collection_and_separate_task_roles(self):
        self.template.resource_count_is("AWS::Events::Rule", 0)
        roles = {
            json.dumps(task["Properties"]["TaskRoleArn"], sort_keys=True)
            for task in self.template.find_resources("AWS::ECS::TaskDefinition").values()
        }
        self.assertEqual(len(roles), 3)

    def test_only_feeder_can_read_job_source_bucket(self):
        tasks = self.template.find_resources("AWS::ECS::TaskDefinition")
        seed = next(task for name, task in tasks.items() if name.startswith("SeedTask"))
        container = seed["Properties"]["ContainerDefinitions"][0]
        environment = {binding["Name"] for binding in container["Environment"]}
        self.assertNotIn("BOARDS_S3_URI", environment)
        self.assertNotIn("Secrets", container)

        for name, policy in self.template.find_resources("AWS::IAM::Policy").items():
            document = json.dumps(policy["Properties"]["PolicyDocument"])
            if "SeedTaskTaskRole" in name:
                self.assertIn("s3:GetObject", document)
                self.assertNotIn("s3:PutObject", document)
                self.assertNotIn("secretsmanager", document)
            elif "ScraperTaskTaskRole" in name or "ClassifierTaskTaskRole" in name:
                self.assertNotIn("s3:", document)

    def test_parallelism_context(self):
        app = App(
            context={
                "maxScrapers": "8",
                "maxClassifiers": "3",
                "scrapeTimeoutSeconds": "30",
            }
        )
        template = Template.from_stack(JobPipelineStack(app, "ConfiguredPipeline"))
        template.resource_count_is("AWS::SecretsManager::Secret", 1)
        template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            {
                "ContainerDefinitions": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Command": ["-m", "backend.jobs.workers.scraper"],
                                "Environment": Match.array_with(
                                    [
                                        {"Name": "SCRAPE_TIMEOUT_SECONDS", "Value": "30"},
                                    ]
                                ),
                            }
                        )
                    ]
                )
            },
        )
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
            JobPipelineStack(App(context={"maxScrapers": "0"}), "InvalidPipeline")


class ImageTagTests(unittest.TestCase):
    def images(self, context):
        template = Template.from_stack(JobPipelineStack(App(context=context), "TagPipeline"))
        tasks = template.find_resources("AWS::ECS::TaskDefinition")
        return " ".join(
            json.dumps(task["Properties"]["ContainerDefinitions"][0]["Image"])
            for task in tasks.values()
        )

    def test_default_tag_is_placeholder(self):
        self.assertIn("unpublished", self.images({}))

    def test_image_tag_applies_to_all_workers(self):
        images = self.images({"imageTag": "abc-1"})
        self.assertEqual(images.count("abc-1"), 3)
        self.assertNotIn("unpublished", images)

    def test_worker_tag_overrides_shared_tag(self):
        images = self.images({"imageTag": "abc-1", "seedImageTag": "def-2"})
        self.assertEqual(images.count("def-2"), 1)
        self.assertEqual(images.count("abc-1"), 2)
