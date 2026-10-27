"""Exercise Jev's actual SDK wire format and DynamoDB writes without external calls."""

import json
import os
import sys
import unittest
from unittest.mock import Mock, patch

import boto3
import httpx2
from moto import mock_aws
from pydantic import ValidationError
from typesafe_sdk import RetryPolicy, TypeSafeClient

from backend.jobs.classification import JobClassification, classify_table
from backend.jobs.classify import main
from backend.jobs.typesafe import JevClassifier


def response_body(seniority="senior", category="software_engineer", relevance="tech"):
    return {
        "model": "jev-latest",
        "usage": {},
        "answers": {
            key: {
                "type": "choice",
                "choice": value,
                "confidence": 0.9,
                "probabilities": {value: 0.9},
            }
            for key, value in {
                "seniority": seniority,
                "category": category,
                "tech_relevance": relevance,
            }.items()
        },
    }


class JevTests(unittest.TestCase):
    def client(self, handler):
        return TypeSafeClient(
            api_key="offline-test-key",
            base_url="https://api.typesafe.ai",
            compression=False,
            transport=httpx2.MockTransport(handler),
            retry=RetryPolicy(max_retries=0),
        )

    def test_real_sdk_sends_three_choice_questions_and_reads_response(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://api.typesafe.ai/v1/systemone")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["authorization"], "Bearer offline-test-key")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "jev-latest")
            self.assertEqual(body["state"], {"title": "Senior Dev", "description": "Python"})
            self.assertEqual(set(body["questions"]), {"seniority", "category", "tech_relevance"})
            for question in body["questions"].values():
                self.assertEqual(question["type"], "choice")
                self.assertIn("untrusted", question["instructions"])
            self.assertIn("ai_engineer", body["questions"]["category"]["criteria"])
            self.assertIn("ml_engineer", body["questions"]["category"]["criteria"])
            return httpx2.Response(200, json=response_body())

        with self.client(handler) as client:
            result = JevClassifier(client)({"title": "Senior Dev", "description": "Python"})
        self.assertEqual(result.seniority, "senior")
        self.assertTrue(result.is_tech)
        self.assertEqual(result.category, "software_engineer")

    def test_non_technical_and_unknown_results(self):
        for category, relevance, tech in [
            ("non_technical", "non_technical", False),
            ("unknown", "unknown", None),
        ]:
            with (
                self.subTest(category=category),
                self.client(
                    lambda _, category=category, relevance=relevance: httpx2.Response(
                        200, json=response_body("unknown", category, relevance)
                    )
                ) as client,
            ):
                result = JevClassifier(client)({"title": "Role", "description": ""})
                self.assertIs(result.is_tech, tech)
                self.assertEqual(result.seniority, "unknown")

    def test_unknown_labels_and_contradictory_output_rejected(self):
        for category, relevance in [
            ("invented_engineer", "tech"),
            ("software_engineer", "non_technical"),
        ]:
            with (
                self.subTest(category=category),
                self.client(
                    lambda _, category=category, relevance=relevance: httpx2.Response(
                        200, json=response_body(category=category, relevance=relevance)
                    )
                ) as client,
                self.assertRaises(ValidationError),
            ):
                JevClassifier(client)({"title": "Role", "description": ""})

    def test_rate_limit_is_retried_but_auth_failure_is_not(self):
        for status, expected_calls in [(429, 2), (401, 1)]:
            calls = []

            def handler(request, calls=calls, status=status):
                calls.append(request)
                if len(calls) == 1:
                    return httpx2.Response(status, json={"error": "unavailable"})
                return httpx2.Response(200, json=response_body())

            from typesafe_sdk import TypeSafeError

            with self.subTest(status=status), self.client(handler) as client:

                def call(client=client):
                    return client.system_one(
                        state="job",
                        questions={"category": {"type": "choice", "criteria": {"tech": None}}},
                        retry=RetryPolicy(max_retries=1, backoff_initial=0, backoff_max=0),
                    )

                if status == 401:
                    with self.assertRaises(TypeSafeError):
                        call()
                else:
                    call()
                self.assertEqual(len(calls), expected_calls)


@mock_aws
class ClassificationPipelineTests(unittest.TestCase):
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
        self.results = boto3.resource("dynamodb", region_name="eu-west-3").create_table(
            TableName="job-classifications",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        self.job = {"title": "Software Engineer", "description": "Build Python services"}
        self.table.put_item(Item={"job_id": "a", "job": self.job, "first_seen": "keep-me"})
        self.classifier = Mock(
            return_value=JobClassification(
                seniority="unknown", is_tech=True, category="software_engineer"
            )
        )

    def run_classifier(self, **kwargs):
        return classify_table(
            self.table,
            self.classifier,
            results_table=self.results,
            provider="typesafe",
            model="jev-latest",
            **kwargs,
        )

    def item(self, key="a"):
        return self.table.get_item(Key={"job_id": key})["Item"]

    def result(self, key="a"):
        return self.results.get_item(Key={"job_id": key}).get("Item", {})

    def test_persists_results_without_changing_original_fields(self):
        counts = self.run_classifier()
        item = self.item()
        self.assertEqual(counts["classified"], 1)
        self.assertEqual(item["job"], self.job)
        self.assertEqual(item["first_seen"], "keep-me")
        self.assertNotIn("classification", item)
        result = self.result()
        self.assertEqual(result["category"], "software_engineer")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["provider"], "typesafe")
        self.assertIn("classified_at", result)
        self.classifier.assert_called_once_with(self.job)

    def test_skips_unchanged_and_reprocesses_changed_jobs(self):
        self.run_classifier()
        self.classifier.reset_mock()
        self.assertEqual(self.run_classifier()["skipped"], 1)
        self.classifier.assert_not_called()
        self.table.update_item(
            Key={"job_id": "a"},
            UpdateExpression="SET #job = :job",
            ExpressionAttributeNames={"#job": "job"},
            ExpressionAttributeValues={":job": {**self.job, "title": "Senior Engineer"}},
        )
        self.assertEqual(self.run_classifier()["classified"], 1)
        self.assertEqual(self.classifier.call_count, 1)

    def test_force_and_model_change_invalidate_saved_results(self):
        self.run_classifier()
        self.assertEqual(self.run_classifier(force=True)["classified"], 1)
        counts = classify_table(
            self.table,
            self.classifier,
            results_table=self.results,
            provider="typesafe",
            model="different-model",
        )
        self.assertEqual(counts["classified"], 1)

    def test_pagination_and_limit_apply_to_calls_not_skipped_items(self):
        self.run_classifier()
        self.table.put_item(Item={"job_id": "b", "job": self.job})
        self.table.put_item(Item={"job_id": "c", "job": self.job})
        self.classifier.reset_mock()
        counts = self.run_classifier(limit=1, page_size=1)
        self.assertEqual(counts["classified"], 1)
        self.assertEqual(self.classifier.call_count, 1)
        self.assertEqual(self.run_classifier(page_size=1)["classified"], 1)
        self.assertTrue(all("category" in self.result(k) for k in ["a", "b", "c"]))

    def test_changed_or_deleted_posting_is_not_overwritten_or_resurrected(self):
        def mutate(job):
            self.table.delete_item(Key={"job_id": "a"})
            return JobClassification(seniority="senior", is_tech=True, category="ai_engineer")

        self.classifier.side_effect = mutate
        self.assertEqual(self.run_classifier()["conflicts"], 1)
        self.assertNotIn("Item", self.table.get_item(Key={"job_id": "a"}))

    def test_concurrent_edit_does_not_get_stale_classification(self):
        def mutate(job):
            self.table.put_item(Item={"job_id": "a", "job": {**job, "title": "New role"}})
            return JobClassification(seniority="senior", is_tech=True, category="ai_engineer")

        self.classifier.side_effect = mutate
        self.assertEqual(self.run_classifier()["conflicts"], 1)
        self.assertNotIn("category", self.result())

    def test_invalid_provider_result_is_not_persisted(self):
        self.classifier.return_value = {
            "seniority": "guru",
            "category": "software_engineer",
            "is_tech": True,
        }
        self.assertEqual(self.run_classifier()["failed"], 1)
        self.assertNotIn("category", self.result())

    def test_invalid_records_do_not_call_provider(self):
        self.table.put_item(Item={"job_id": "a", "job": {"description": "no title"}})
        self.assertEqual(self.run_classifier()["failed"], 1)
        self.classifier.assert_not_called()

    def test_cli_end_to_end_with_actual_sdk_and_mocked_api(self):
        with (
            TypeSafeClient(
                api_key="offline-test-key",
                base_url="https://api.typesafe.ai",
                compression=False,
                transport=httpx2.MockTransport(
                    lambda _: httpx2.Response(200, json=response_body())
                ),
            ) as client,
            patch("backend.jobs.classify.TypeSafeClient", return_value=client),
            patch.dict(os.environ, {"TYPESAFE_API_KEY": "offline-test-key"}),
            patch.object(sys, "argv", ["classify", "--aws-region", "eu-west-3", "--limit", "1"]),
        ):
            self.assertEqual(main(), 0)
        self.assertEqual(self.result()["category"], "software_engineer")

    def test_missing_key_fails_before_accessing_aws(self):
        with (
            patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}),
            patch.object(sys, "argv", ["classify"]),
            patch("backend.jobs.classify.DynamoJobStore") as store,
        ):
            self.assertEqual(main(), 1)
            store.assert_not_called()
