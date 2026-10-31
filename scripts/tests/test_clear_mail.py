"""Clearing mail data forgets what was read and keeps what the user chose."""

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import boto3
from moto import mock_aws

from scripts.clear_mail import main

ITEMS = {
    "sync": {},
    "alert#2026#a": {},
    "application#a1": {},
    "message#m1": {},
    "job#1": {},
    "job#2": {"description": "Saved for a tailored CV"},
    "interest#linked": {"application_id": "a1"},
    "interest#free": {},
}


@mock_aws
class ClearMailTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(
            os.environ, {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "test"}
        )
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        self.table = boto3.resource("dynamodb").create_table(
            TableName="mail",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "item_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "item_id", "AttributeType": "S"},
            ],
        )
        for item_id, values in ITEMS.items():
            self.table.put_item(Item={"user_id": "ali", "item_id": item_id, **values})

    def run_script(self, *arguments):
        output = io.StringIO()
        with (
            patch("scripts.clear_mail.stack_outputs", return_value={"MailTable": "mail"}),
            patch("sys.argv", ["clear_mail", *arguments]),
            redirect_stdout(output),
        ):
            return main(), output.getvalue()

    def stored(self):
        return {item["item_id"]: item for item in self.table.scan()["Items"]}

    def test_without_yes_it_only_reports(self):
        code, output = self.run_script()
        self.assertEqual(code, 0)
        self.assertIn("5 items to remove", output)
        self.assertIn("1 opened jobs get unlinked", output)
        self.assertEqual(len(self.stored()), len(ITEMS))
        self.assertIn("application_id", self.stored()["interest#linked"])

    def test_yes_keeps_opened_jobs_and_saved_descriptions(self):
        code, _ = self.run_script("--yes")
        self.assertEqual(code, 0)
        stored = self.stored()
        self.assertCountEqual(stored, ["job#2", "interest#linked", "interest#free"])
        self.assertNotIn("application_id", stored["interest#linked"])
