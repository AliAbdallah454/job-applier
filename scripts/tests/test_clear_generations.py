"""Clearing generations removes generated data and nothing a user uploaded."""

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import boto3
from moto import mock_aws

from scripts.clear_generations import delete_versions, main

OUTPUTS = {"DocumentGenerationsTable": "generations", "DocumentsBucketName": "documents"}
CV = "users/ali/cvs/abc/cv.tex"
GENERATED = "users/ali/generations/g1/cv.pdf"
# A user whose id is "generations" still has an uploaded CV, not generated files.
ODD_CV = "users/generations/cvs/abc/cv.tex"


@mock_aws
class ClearGenerationsTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(
            os.environ, {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "test"}
        )
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        self.table = boto3.resource("dynamodb").create_table(
            TableName="generations",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "generation_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "generation_id", "AttributeType": "S"},
            ],
        )
        self.table.put_item(Item={"user_id": "ali", "generation_id": "g1"})
        self.s3 = boto3.client("s3")
        self.s3.create_bucket(Bucket="documents")
        self.s3.put_bucket_versioning(
            Bucket="documents", VersioningConfiguration={"Status": "Enabled"}
        )
        for key in (CV, ODD_CV, GENERATED, GENERATED):
            self.s3.put_object(Bucket="documents", Key=key, Body=b"file")
        self.s3.delete_object(Bucket="documents", Key=GENERATED)

    def run_script(self, *arguments):
        output = io.StringIO()
        with (
            patch("scripts.clear_generations.stack_outputs", return_value=OUTPUTS),
            patch("sys.argv", ["clear_generations", *arguments]),
            redirect_stdout(output),
        ):
            return main(), output.getvalue()

    def stored(self):
        listing = self.s3.list_object_versions(Bucket="documents")
        return [
            item["Key"] for item in listing.get("Versions", []) + listing.get("DeleteMarkers", [])
        ]

    def test_without_yes_it_only_reports(self):
        code, output = self.run_script()

        self.assertEqual(code, 0)
        self.assertIn("1 records in generations; 3 file versions in documents.", output)
        self.assertIn("Nothing was deleted", output)
        self.assertEqual(self.table.scan()["Count"], 1)
        self.assertEqual(self.stored().count(GENERATED), 3)

    def test_yes_removes_every_version_and_keeps_uploaded_cvs(self):
        code, _ = self.run_script("--yes")

        self.assertEqual(code, 0)
        self.assertEqual(self.table.scan()["Count"], 0)
        self.assertCountEqual(self.stored(), [CV, ODD_CV])

    def test_a_file_that_could_not_be_deleted_is_an_error(self):
        s3 = Mock()
        s3.delete_objects.return_value = {"Errors": [{"Key": "a", "Message": "Access Denied"}]}

        with self.assertRaisesRegex(ValueError, "a: Access Denied"):
            delete_versions(s3, "documents", [{"Key": "a", "VersionId": "1"}])
