"""Collection YAML and S3 sources work for manual and scheduled feeder runs."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import boto3
import yaml
from botocore.exceptions import ClientError
from moto import mock_aws
from pydantic import ValidationError

from backend.jobs.collection_config import (
    CollectionConfig,
    collection_environment,
    load_collection_config,
    s3_location,
)
from backend.jobs.workers.seed import main as seed_main


@mock_aws
class CollectionConfigTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        self.s3 = boto3.client("s3")
        self.s3.create_bucket(Bucket="job-sources")
        self.s3.put_bucket_versioning(
            Bucket="job-sources", VersioningConfiguration={"Status": "Enabled"}
        )
        self.sqs = boto3.client("sqs")
        self.queue = self.sqs.create_queue(QueueName="boards")["QueueUrl"]
        os.environ["QUEUE_URL"] = self.queue
        os.environ["BOARDS_S3_URI"] = "s3://job-sources/boards.csv"
        directory = "platform,token\n" + "".join(f"greenhouse,company{i}\n" for i in range(105))
        self.put("boards.csv", directory + "lever,example\ngreenhouse,company0\n")
        self.write_config()

    def put(self, key, body):
        self.s3.put_object(Bucket="job-sources", Key=key, Body=body)

    def write_config(self, **settings):
        config = CollectionConfig(boards_key="boards.csv", **settings)
        os.environ.update(collection_environment(config, "job-sources"))

    def run_seed(self, *arguments):
        with patch.object(sys, "argv", ["seed", *arguments]), redirect_stdout(io.StringIO()):
            seed_main()

    def count(self):
        return int(
            self.sqs.get_queue_attributes(
                QueueUrl=self.queue, AttributeNames=["ApproximateNumberOfMessages"]
            )["Attributes"]["ApproximateNumberOfMessages"]
        )

    def messages(self):
        return [
            json.loads(message["Body"])
            for message in self.sqs.receive_message(QueueUrl=self.queue, MaxNumberOfMessages=10)[
                "Messages"
            ]
        ]

    def test_daily_environment_reads_all_boards_and_jobs_without_duplicates(self):
        self.run_seed()
        self.assertEqual(self.count(), 106)
        self.assertTrue(all(message["max_jobs_per_board"] is None for message in self.messages()))

    def test_yaml_filters_and_limits_are_overridden_by_explicit_flags(self):
        self.write_config(platforms=["greenhouse"], max_boards=3, max_jobs_per_board=150)
        self.run_seed("--platforms", "lever", "--max-boards", "1", "--max-jobs-per-board", "2")
        self.assertEqual(self.count(), 1)
        message = self.messages()[0]
        self.assertEqual(message["board"]["source"], "lever")
        self.assertEqual(message["max_jobs_per_board"], 2)

    def test_yaml_limits_and_current_s3_contents_are_used_each_run(self):
        self.write_config(max_boards=2, max_jobs_per_board=150)
        self.run_seed()
        self.assertEqual(self.count(), 2)
        self.assertTrue(all(message["max_jobs_per_board"] == 150 for message in self.messages()))
        self.put("boards.csv", "platform,token\nlever,newcompany\n")
        self.write_config(platforms=["lever"], max_jobs_per_board=4)
        self.run_seed()
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.messages()[0]["board"]["board"], "newcompany")

    def test_missing_or_invalid_source_never_queues_partial_work(self):
        for contents in ["platform,token\ngreenhouse,invalid/token\n", "wrong,columns\na,b\n"]:
            with self.subTest(contents=contents):
                self.put("boards.csv", contents)
                with self.assertRaises(ValueError):
                    self.run_seed()
                self.assertEqual(self.count(), 0)
        self.s3.delete_object(Bucket="job-sources", Key="boards.csv")
        with self.assertRaises(ClientError) as error:
            self.run_seed()
        self.assertEqual(error.exception.response["Error"]["Code"], "NoSuchKey")
        self.assertEqual(self.count(), 0)

    def test_local_yaml_is_compiled_to_settings_without_s3_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.yaml"
            path.write_text("boards_key: boards.csv\nmax_boards: 2\n")
            config = load_collection_config(str(path))
        os.environ.update(collection_environment(config, "job-sources"))
        self.run_seed()
        self.assertEqual(self.count(), 2)
        objects = self.s3.list_objects_v2(Bucket="job-sources")["Contents"]
        self.assertEqual([obj["Key"] for obj in objects], ["boards.csv"])

    def test_invalid_settings_and_unsafe_yaml_are_rejected(self):
        for settings in [
            {"max_boards": 0},
            {"max_jobs_per_board": -1},
            {"max_boards": True},
            {"platforms": ["unknown"]},
            {"platforms": []},
            {"max_jobs_per_board": "20"},
            {"unknown_setting": 1},
            {"boards_key": "https://example.com/boards.csv"},
            {"boards_key": "s3://job-sources/boards.csv"},
            {"boards_key": "/boards.csv"},
            {"boards_key": "boards.txt"},
            {"boards_key": ""},
        ]:
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                CollectionConfig.model_validate({"boards_key": "boards.csv", **settings})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.yaml"
            path.write_text("!!python/object/apply:os.system ['echo unsafe']")
            with self.assertRaises(yaml.YAMLError):
                load_collection_config(str(path))

    def test_s3_uris_require_a_bucket_and_object_without_query_parameters(self):
        for uri in [
            "s3://bucket",
            "s3:///file.csv",
            "s3://bucket/file.csv?x=1",
            "s3://bucket/file.csv#x",
        ]:
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                s3_location(uri)
