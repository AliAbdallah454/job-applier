"""Repository isolation and deployment ordering; no Docker or AWS calls."""

import json
import unittest
from unittest.mock import Mock, patch

from aws_cdk import App, Stack
from aws_cdk import aws_ecs as ecs
from aws_cdk.assertions import Match, Template

from infra.image_publisher.handler import is_complete, on_event
from infra.worker_images import WorkerImages


class WorkerImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stack = Stack(App(), "Images", stack_name="JobPipeline-Dev")
        publisher = WorkerImages(stack, "WorkerImages")
        definition = ecs.FargateTaskDefinition(stack, "Task")
        definition.add_container(
            "Scraper", image=publisher.image("scraper", "containers/Dockerfile.scrape")
        )
        cls.template = Template.from_stack(stack)

    def test_repository_is_named_for_stack_and_worker_and_retained(self):
        self.template.has_resource(
            "AWS::ECR::Repository",
            {
                "DeletionPolicy": "Retain",
                "UpdateReplacePolicy": "Retain",
                "Properties": Match.object_like(
                    {
                        "RepositoryName": "jobpipeline-dev/scraper",
                        "ImageTagMutability": "IMMUTABLE",
                        "ImageScanningConfiguration": {"ScanOnPush": True},
                    }
                ),
            },
        )

    def test_task_image_waits_for_successful_publication(self):
        publication_id, publication = next(
            iter(self.template.find_resources("Custom::WorkerImage").items())
        )
        task = next(iter(self.template.find_resources("AWS::ECS::TaskDefinition").values()))
        image = task["Properties"]["ContainerDefinitions"][0]["Image"]
        self.assertIn(publication_id, json.dumps(image))
        self.assertIn("ImageTag", json.dumps(image))
        self.assertIn("workerimages", json.dumps(image).lower())
        self.assertIn("DependsOn", publication)
        repository_id = publication["Properties"]["RepositoryName"]["Ref"]
        repository = self.template.find_resources("AWS::ECR::Repository")[repository_id]
        self.assertEqual(repository["Properties"]["RepositoryName"], "jobpipeline-dev/scraper")
        registry = publication["Properties"]["Registry"]["Fn::Select"]
        self.assertEqual(registry[0], 0)
        self.assertEqual(registry[1]["Fn::Split"][0], "/")
        self.assertRegex(publication["Properties"]["ImageTag"], r"^[a-f0-9]{64}$")

    def test_copy_project_is_deployment_only_and_has_no_source_or_artifacts(self):
        self.template.resource_count_is("AWS::CodeBuild::Project", 1)
        self.template.has_resource_properties(
            "AWS::CodeBuild::Project",
            {
                "Source": Match.object_like({"Type": "NO_SOURCE"}),
                "Artifacts": {"Type": "NO_ARTIFACTS"},
                "Environment": Match.object_like({"PrivilegedMode": True}),
                "TimeoutInMinutes": 30,
            },
        )
        projects = self.template.find_resources("AWS::CodeBuild::Project")
        self.assertNotIn("VpcConfig", next(iter(projects.values()))["Properties"])
        self.template.has_output("ScraperRepositoryUri", {"Value": Match.any_value()})

    def test_worker_execution_role_pulls_only_from_its_worker_repository(self):
        policies = [
            value["Properties"]["PolicyDocument"]
            for key, value in self.template.find_resources("AWS::IAM::Policy").items()
            if "TaskExecutionRole" in key
        ]
        self.assertEqual(len(policies), 1)
        serialized = json.dumps(policies[0])
        self.assertIn("scraperRepository", serialized)
        self.assertNotIn("container-assets", serialized)
        self.assertNotIn("ecr:PutImage", serialized)


class PublisherHandlerTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "RequestType": "Create",
            "RequestId": "request-1",
            "LogicalResourceId": "ScraperPublication",
            "ResourceProperties": {
                "SourceImage": "registry/staging:hash",
                "DestinationImage": "registry/jobs/scraper:hash",
                "RepositoryName": "jobs/scraper",
                "ImageTag": "hash",
                "Registry": "registry",
            },
        }
        self.client = Mock()
        self.client.start_build.return_value = {"build": {"id": "copy:build-1"}}

    def test_start_uses_configured_project_and_idempotency_token(self):
        with (
            patch.dict("os.environ", {"PROJECT_NAME": "copy-project"}),
            patch("infra.image_publisher.handler.boto3.client", return_value=self.client),
        ):
            first = on_event(self.event, None)
            second = on_event(self.event, None)
        self.assertEqual(first, second)
        arguments = self.client.start_build.call_args.kwargs
        self.assertEqual(arguments["projectName"], "copy-project")
        self.assertEqual(len(arguments["idempotencyToken"]), 64)
        self.assertEqual(first["Data"]["ImageTag"], "hash")
        self.assertEqual(first["Data"]["BuildId"], "copy:build-1")
        self.assertEqual(len(arguments["environmentVariablesOverride"]), 5)

    def test_delete_never_copies_or_deletes_images(self):
        self.event.update(RequestType="Delete", PhysicalResourceId="existing")
        with patch("infra.image_publisher.handler.boto3.client") as client:
            self.assertEqual(on_event(self.event, None), {"PhysicalResourceId": "existing"})
            self.assertTrue(is_complete(self.event, None)["IsComplete"])
            client.assert_not_called()

    def test_waiter_only_completes_on_success_and_reports_failed_builds(self):
        self.event["Data"] = {"BuildId": "copy:build-1"}
        for status in ["IN_PROGRESS", "SUCCEEDED", "FAILED", "FAULT", "STOPPED", "TIMED_OUT"]:
            with (
                self.subTest(status=status),
                patch("infra.image_publisher.handler.boto3.client", return_value=self.client),
            ):
                self.client.batch_get_builds.return_value = {"builds": [{"buildStatus": status}]}
                if status in {"IN_PROGRESS", "SUCCEEDED"}:
                    result = is_complete(self.event, None)
                    self.assertEqual(result["IsComplete"], status == "SUCCEEDED")
                    if status == "SUCCEEDED":
                        self.assertEqual(result["Data"]["ImageTag"], "hash")
                else:
                    with self.assertRaisesRegex(RuntimeError, "copy:build-1"):
                        is_complete(self.event, None)

    def test_missing_build_is_an_explicit_failure(self):
        self.event["Data"] = {"BuildId": "copy:missing"}
        self.client.batch_get_builds.return_value = {"builds": []}
        with patch("infra.image_publisher.handler.boto3.client", return_value=self.client):
            with self.assertRaisesRegex(RuntimeError, "not found"):
                is_complete(self.event, None)
