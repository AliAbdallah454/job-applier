"""Operator helpers must launch the configured task and never expose API keys."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from infra.manage import launch, main


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.outputs = {
            "ClusterName": "cluster",
            "SeedTaskDefinition": "task",
            "PublicSubnets": "subnet-a,subnet-b",
            "SecurityGroupId": "sg",
            "TypesafeSecretArn": "secret",
        }
        self.session = Mock()

    def test_launch_passes_bounded_overrides_and_public_network(self):
        self.session.client.return_value.run_task.return_value = {"tasks": [{"taskArn": "run"}]}
        self.assertEqual(
            launch(
                self.session,
                self.outputs,
                mode="scrape",
                max_boards=3,
                max_jobs_per_board=5,
                platforms=["greenhouse"],
            ),
            ["run"],
        )
        args = self.session.client.return_value.run_task.call_args.kwargs
        self.assertEqual(args["launchType"], "FARGATE")
        self.assertEqual(args["count"], 1)
        self.assertEqual(
            args["networkConfiguration"]["awsvpcConfiguration"]["subnets"],
            ["subnet-a", "subnet-b"],
        )
        self.assertEqual(
            args["overrides"]["containerOverrides"][0]["command"],
            [
                "-m",
                "backend.jobs.workers.seed",
                "--mode",
                "scrape",
                "--max-boards",
                "3",
                "--max-jobs-per-board",
                "5",
                "--platforms",
                "greenhouse",
            ],
        )

    def test_failed_launch_is_not_reported_as_success(self):
        self.session.client.return_value.run_task.return_value = {
            "tasks": [],
            "failures": [{"reason": "RESOURCE"}],
        }
        with self.assertRaises(RuntimeError):
            launch(self.session, self.outputs, mode="classify")

    def test_secure_key_prompt_sends_value_only_to_secrets_manager(self):
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["manage", "set-key"]),
            patch("infra.manage.boto3.Session", return_value=self.session),
            patch("infra.manage.stack_outputs", return_value=self.outputs),
            patch("infra.manage.getpass.getpass", return_value="offline-test-secret"),
            redirect_stdout(output),
        ):
            main()
        self.session.client.return_value.put_secret_value.assert_called_once_with(
            SecretId="secret", SecretString="offline-test-secret"
        )
        self.assertNotIn("offline-test-secret", output.getvalue())

    def test_collection_checks_key_metadata_without_retrieving_it(self):
        self.session.client.return_value.describe_secret.return_value = {
            "VersionIdsToStages": {"version": ["AWSCURRENT"]}
        }
        with (
            patch.object(sys, "argv", ["manage", "collect"]),
            patch("infra.manage.boto3.Session", return_value=self.session),
            patch("infra.manage.stack_outputs", return_value=self.outputs),
            patch("infra.manage.launch", return_value=["run"]) as run,
            redirect_stdout(io.StringIO()),
        ):
            main()
        run.assert_called_once()
        self.session.client.return_value.get_secret_value.assert_not_called()

    def test_empty_secret_prevents_launch(self):
        self.session.client.return_value.describe_secret.return_value = {}
        with (
            patch.object(sys, "argv", ["manage", "collect"]),
            patch("infra.manage.boto3.Session", return_value=self.session),
            patch("infra.manage.stack_outputs", return_value=self.outputs),
            patch("infra.manage.launch") as run,
            self.assertRaises(SystemExit),
        ):
            main()
        run.assert_not_called()
