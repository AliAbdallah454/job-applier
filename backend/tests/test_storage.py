"""Exercise DynamoDB persistence and the CLI without accessing AWS."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.jobs.__main__ import main
from backend.jobs.models import JobPost
from backend.jobs.storage import DynamoJobStore


@mock_aws
class DynamoStorageTests(unittest.TestCase):
    def setUp(self):
        # Tests use Moto's fake credentials, independent of any host AWS profile.
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        self.table = boto3.resource("dynamodb", region_name="eu-west-3").create_table(
            TableName="job-postings",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        self.job = {
            "source": "greenhouse",
            "board": "example",
            "region": "global",
            "source_id": "123",
            "company": "Example",
            "title": "Engineer",
            "description": "Python",
            "posting_url": "https://example.com/jobs/123",
        }

    def store(self):
        return DynamoJobStore("job-postings", region="eu-west-3")

    def items(self):
        return self.table.scan()["Items"]

    def test_upsert_preserves_first_seen_and_updates_posting(self):
        self.store().save_jobs([self.job])
        original = self.items()[0]
        self.store().save_jobs([{**self.job, "title": "Senior Engineer"}])
        updated = self.items()
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["first_seen"], original["first_seen"])
        self.assertGreaterEqual(updated[0]["last_seen"], original["last_seen"])
        self.assertEqual(updated[0]["job"]["title"], "Senior Engineer")

    def test_board_and_region_and_url_fallback_distinguish_jobs(self):
        self.store().save_jobs(
            [
                self.job,
                {**self.job, "board": "other"},
                {**self.job, "region": "eu"},
                {**self.job, "source_id": None},
                {**self.job, "source_id": None, "posting_url": "https://example.com/jobs/456"},
            ]
        )
        self.assertEqual(len(self.items()), 5)

    def test_snapshots_do_not_rewrite_previous_jobs(self):
        store = self.store()
        store.save_report({"jobs": [self.job]})
        original = self.items()[0]
        store.save_report({"jobs": [self.job]})
        self.assertEqual(self.items(), [original])
        store.save_report({"jobs": [self.job, {**self.job, "source_id": "456"}]})
        self.assertEqual(len(self.items()), 2)

    def test_wrong_key_and_missing_table_fail_before_collection(self):
        boto3.resource("dynamodb", region_name="eu-west-3").create_table(
            TableName="wrong",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        with self.assertRaises(ValueError):
            DynamoJobStore("wrong", region="eu-west-3")
        with (
            patch.object(
                sys,
                "argv",
                [
                    "jobs",
                    "--source",
                    "greenhouse",
                    "--board",
                    "example",
                    "--table",
                    "missing",
                    "--aws-region",
                    "eu-west-3",
                ],
            ),
            patch("backend.jobs.__main__.create_fetcher") as factory,
        ):
            self.assertEqual(main(), 1)
            factory.assert_not_called()

    def test_single_board_cli_defaults_to_dynamodb(self):
        with (
            patch.object(
                sys,
                "argv",
                [
                    "jobs",
                    "--source",
                    "greenhouse",
                    "--board",
                    "example",
                    "--aws-region",
                    "eu-west-3",
                ],
            ),
            patch("backend.jobs.__main__.create_fetcher") as factory,
        ):
            factory.return_value.fetch.return_value = [JobPost.model_validate(self.job)]
            self.assertEqual(main(), 0)
        self.assertEqual(self.items()[0]["job"]["board"], "example")

    def test_batch_cli_persists_completed_boards_before_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            boards = Path(directory) / "boards.json"
            boards.write_text(
                json.dumps(
                    [
                        {"source": "greenhouse", "board": "example"},
                        {"source": "greenhouse", "board": "broken"},
                    ]
                )
            )
            from backend.jobs.job_fetchers import FetchError

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "jobs",
                        "--boards-file",
                        str(boards),
                        "--aws-region",
                        "eu-west-3",
                    ],
                ),
                patch("backend.jobs.collector.create_fetcher") as factory,
            ):
                factory.return_value.fetch.side_effect = [
                    [JobPost.model_validate(self.job)],
                    FetchError("unavailable"),
                ]
                self.assertEqual(main(), 2)
            self.assertEqual(len(self.items()), 1)
            self.assertEqual(list(Path(directory).iterdir()), [boards])

    def test_write_failure_is_reported_as_failure(self):
        error = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "UpdateItem"
        )
        with (
            patch.object(
                sys,
                "argv",
                [
                    "jobs",
                    "--source",
                    "greenhouse",
                    "--board",
                    "example",
                    "--aws-region",
                    "eu-west-3",
                ],
            ),
            patch("backend.jobs.__main__.create_fetcher") as factory,
            patch.object(DynamoJobStore, "save_jobs", side_effect=error),
        ):
            factory.return_value.fetch.return_value = [JobPost.model_validate(self.job)]
            self.assertEqual(main(), 1)
        self.assertEqual(self.items(), [])
