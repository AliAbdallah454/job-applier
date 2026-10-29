"""Demo seeding resets its own records without deleting other users' profiles."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import boto3
from moto import mock_aws

from backend.profiles.models import CandidateProfile
from scripts.seed_profiles import (
    DEFAULT_FIXTURE,
    load_demo_profiles,
    main,
    reset_demo_profiles,
    resolve_table_name,
)


@mock_aws
class ProfileSeedTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        self.table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="profiles",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
        )
        self.profiles = load_demo_profiles(DEFAULT_FIXTURE)

    def test_reset_removes_old_demo_records_replaces_edits_and_preserves_real_users(self):
        real_profile = {"user_id": "real-user", "full_name": "Preserve Me"}
        self.table.put_item(Item=real_profile)
        self.table.put_item(Item={"user_id": "demo-user-obsolete", "full_name": "Old demo"})

        first = reset_demo_profiles(self.table, self.profiles)
        self.assertEqual(first, {"deleted": 1, "inserted": len(self.profiles)})
        user_id = self.profiles[0].user_id
        self.table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET full_name = :name",
            ExpressionAttributeValues={":name": "Edited demo"},
        )

        self.assertEqual(
            reset_demo_profiles(self.table, self.profiles),
            {"deleted": len(self.profiles), "inserted": len(self.profiles)},
        )
        self.assertEqual(self.table.get_item(Key={"user_id": "real-user"})["Item"], real_profile)
        stored = self.table.get_item(Key={"user_id": user_id})["Item"]
        self.assertEqual(CandidateProfile.model_validate(stored), self.profiles[0])
        self.assertNotIn("Item", self.table.get_item(Key={"user_id": "demo-user-obsolete"}))

    def test_ali_profile_is_valid_and_replaced_when_reseeding(self):
        ali = next(profile for profile in self.profiles if profile.user_id == "ali")
        self.assertEqual(ali.full_name, "Ali Abdallah")
        self.assertEqual(len(ali.experiences), 2)
        self.assertEqual(len(ali.projects), 3)
        self.assertEqual(len(ali.education), 2)
        self.table.put_item(Item={"user_id": "ali", "full_name": "Outdated profile"})
        result = reset_demo_profiles(self.table, self.profiles)
        self.assertEqual(result["deleted"], 1)
        stored = self.table.get_item(Key={"user_id": "ali"})["Item"]
        self.assertEqual(CandidateProfile.model_validate(stored), ali)

    def test_ali_is_preserved_when_not_in_selected_fixture(self):
        existing = {"user_id": "ali", "full_name": "Preserve this profile"}
        self.table.put_item(Item=existing)
        demos = [profile for profile in self.profiles if profile.user_id.startswith("demo-user-")]
        reset_demo_profiles(self.table, demos)
        self.assertEqual(self.table.get_item(Key={"user_id": "ali"})["Item"], existing)

    def test_reset_follows_scan_pagination_before_deleting(self):
        with self.table.batch_writer() as batch:
            for index in range(105):
                batch.put_item(Item={"user_id": f"demo-user-old-{index}"})
        self.assertEqual(
            reset_demo_profiles(self.table, self.profiles),
            {"deleted": 105, "inserted": len(self.profiles)},
        )
        self.assertEqual(self.table.scan(Select="COUNT")["Count"], len(self.profiles))

    def test_empty_duplicate_or_real_user_input_is_rejected_before_deletion(self):
        self.table.put_item(Item={"user_id": "demo-user-keep"})
        for profiles in [
            [],
            [self.profiles[0], self.profiles[0]],
            [CandidateProfile(user_id="real-user", full_name="Example")],
        ]:
            with self.subTest(profiles=profiles), self.assertRaises(ValueError):
                reset_demo_profiles(self.table, profiles)
        self.assertIn("Item", self.table.get_item(Key={"user_id": "demo-user-keep"}))

    def test_invalid_fixture_fails_before_any_aws_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps([{"user_id": "demo-user-invalid"}]))
            with (
                patch.object(sys, "argv", ["seed", "--file", str(path)]),
                patch("scripts.seed_profiles.boto3.Session") as session,
                patch("sys.stderr", new_callable=io.StringIO),
            ):
                self.assertEqual(main(), 1)
                session.assert_not_called()

    def test_wrong_table_schema_is_rejected(self):
        wrong = Mock(key_schema=[{"AttributeName": "job_id", "KeyType": "HASH"}])
        with self.assertRaises(ValueError):
            reset_demo_profiles(wrong, self.profiles)
        wrong.batch_writer.assert_not_called()

    def test_cli_resolves_application_output_and_seeds_that_table(self):
        session = Mock()
        session.client.return_value.describe_stacks.return_value = {
            "Stacks": [{"Outputs": [{"OutputKey": "ProfilesTable", "OutputValue": "profiles"}]}]
        }
        session.resource.return_value.Table.return_value = self.table
        with (
            patch.object(sys, "argv", ["seed", "--stack", "Application", "--region", "us-east-1"]),
            patch("scripts.seed_profiles.boto3.Session", return_value=session),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(), 0)
        session.client.return_value.describe_stacks.assert_called_once_with(StackName="Application")
        session.resource.return_value.Table.assert_called_once_with("profiles")
        self.assertEqual(self.table.scan(Select="COUNT")["Count"], len(self.profiles))

    def test_missing_profiles_output_is_a_clear_error(self):
        session = Mock()
        session.client.return_value.describe_stacks.return_value = {"Stacks": [{"Outputs": []}]}
        with self.assertRaisesRegex(ValueError, "ProfilesTable"):
            resolve_table_name(session, "JobPipeline")
