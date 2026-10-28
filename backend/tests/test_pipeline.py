"""Queue and stream contracts, distributed classification and separate-table joins."""

import json
import os
import sys
import time
import unittest
from unittest.mock import Mock, patch

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from moto import mock_aws

from backend.jobs.classification.models import JobClassification
from backend.jobs.collector import BoardConfig
from backend.jobs.models import JobPost
from backend.jobs.storage.classifications import ClassificationBusy, ClassificationStore
from backend.jobs.storage.jobs import DynamoJobStore
from backend.jobs.workers.classifier import process_classify
from backend.jobs.workers.runtime import handle_message
from backend.jobs.workers.scraper import process_scrape
from backend.jobs.workers.seed import backfill, enqueue
from backend.jobs.workers.seed import main as seed_main
from infra.stream_router.handler import handler, needs_classification


@mock_aws
class PipelineTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        os.environ["AWS_DEFAULT_REGION"] = "eu-west-3"
        dynamodb = boto3.resource("dynamodb")
        self.jobs = dynamodb.create_table(
            TableName="jobs",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
        )
        self.results = dynamodb.create_table(
            TableName="results",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ["job_id", "category", "seniority_job"]
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "ByCategorySeniority",
                    "KeySchema": [
                        {"AttributeName": "category", "KeyType": "HASH"},
                        {"AttributeName": "seniority_job", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        self.store = ClassificationStore(self.jobs, self.results)
        self.sqs = boto3.client("sqs")
        self.queue = self.sqs.create_queue(QueueName="work")["QueueUrl"]
        os.environ["QUEUE_URL"] = self.queue
        self.job = {
            "source": "greenhouse",
            "board": "example",
            "region": "global",
            "source_id": "123",
            "company": "Example",
            "title": "Cloud Engineer",
            "description": "Senior AWS cloud infrastructure engineer",
            "posting_url": "https://example.com/jobs/123",
        }
        self.classifier = Mock(
            return_value=JobClassification(
                contract_type=None, seniority="senior", is_tech=True, category="cloud_engineer"
            )
        )

    def message(self, body):
        self.sqs.send_message(QueueUrl=self.queue, MessageBody=json.dumps(body))
        return self.sqs.receive_message(QueueUrl=self.queue)["Messages"][0]

    def item(self):
        return self.jobs.scan()["Items"][0]

    def result(self, job_id):
        return self.results.get_item(Key={"job_id": job_id}).get("Item", {})

    def test_seed_deduplicates_boards_and_preserves_region(self):
        board = BoardConfig(source="lever", board="example", region="eu")
        self.assertEqual(enqueue(self.sqs, self.queue, [board, board], max_jobs_per_board=5), 1)
        body = json.loads(self.sqs.receive_message(QueueUrl=self.queue)["Messages"][0]["Body"])
        self.assertEqual(body["board"]["region"], "eu")
        self.assertEqual(body["max_jobs_per_board"], 5)

    def test_seed_defaults_to_all_boards_and_all_postings(self):
        boards = [BoardConfig(source="greenhouse", board=f"company{i}") for i in range(105)]
        with (
            patch.dict(
                os.environ,
                {"QUEUE_URL": self.queue, "MAX_BOARDS": "all", "JOBS_PER_BOARD": "all"},
                clear=True,
            ),
            patch.object(sys, "argv", ["seed"]),
            patch("backend.jobs.workers.seed.boto3.client", return_value=self.sqs),
            patch("backend.jobs.workers.seed.load_boards", return_value=boards),
            patch("backend.jobs.workers.seed.enqueue", return_value=105) as send,
            patch("builtins.print"),
        ):
            seed_main()

        self.assertEqual(len(send.call_args.args[2]), 105)
        self.assertIsNone(send.call_args.kwargs["max_jobs_per_board"])

    def test_seed_explicit_limits_can_exceed_100_jobs(self):
        boards = [BoardConfig(source="greenhouse", board=f"company{i}") for i in range(3)]
        with (
            patch.dict(os.environ, {"QUEUE_URL": self.queue}, clear=True),
            patch.object(sys, "argv", ["seed", "--max-boards", "2", "--max-jobs-per-board", "150"]),
            patch("backend.jobs.workers.seed.boto3.client", return_value=self.sqs),
            patch("backend.jobs.workers.seed.load_boards", return_value=boards),
            patch("backend.jobs.workers.seed.enqueue", return_value=2) as send,
            patch("builtins.print"),
        ):
            seed_main()

        self.assertEqual(len(send.call_args.args[2]), 2)
        self.assertEqual(send.call_args.kwargs["max_jobs_per_board"], 150)

    def test_unlimited_queue_message_reaches_fetcher_without_a_cap(self):
        board = BoardConfig(source="greenhouse", board="example")
        enqueue(self.sqs, self.queue, [board])
        body = json.loads(self.sqs.receive_message(QueueUrl=self.queue)["Messages"][0]["Body"])

        with patch("backend.jobs.workers.scraper.create_fetcher") as factory:
            fetcher = factory.return_value.__enter__.return_value
            fetcher.fetch.return_value = [JobPost.model_validate(self.job)] * 105
            store = Mock()
            self.assertEqual(process_scrape(body, store), 105)

        fetcher.fetch.assert_called_once_with(limit=None)
        self.assertEqual(len(store.save_jobs.call_args.args[0]), 105)

    def test_scrape_to_stream_to_queue_to_results_and_filter_index(self):
        with patch("backend.jobs.workers.scraper.create_fetcher") as factory:
            factory.return_value.__enter__.return_value.fetch.return_value = [
                JobPost.model_validate(self.job)
            ]
            self.assertEqual(
                process_scrape(
                    {"board": {"source": "greenhouse", "board": "example"}, "limit": 5},
                    DynamoJobStore("jobs"),
                ),
                1,
            )
        item = self.item()
        serialized = {key: TypeSerializer().serialize(value) for key, value in item.items()}
        response = handler(
            {
                "Records": [
                    {
                        "eventName": "INSERT",
                        "dynamodb": {"NewImage": serialized, "SequenceNumber": "1"},
                    }
                ]
            },
            None,
        )
        self.assertEqual(response["batchItemFailures"], [])
        message = self.sqs.receive_message(QueueUrl=self.queue)["Messages"][0]
        handle_message(
            self.sqs,
            self.queue,
            message,
            lambda body: process_classify(
                body, self.jobs, self.store, self.classifier, "jev-latest"
            ),
        )
        self.assertEqual(
            self.sqs.get_queue_attributes(
                QueueUrl=self.queue, AttributeNames=["ApproximateNumberOfMessagesNotVisible"]
            )["Attributes"]["ApproximateNumberOfMessagesNotVisible"],
            "0",
        )
        result = self.results.query(
            IndexName="ByCategorySeniority",
            KeyConditionExpression=Key("category").eq("cloud_engineer")
            & Key("seniority_job").begins_with("senior#"),
        )["Items"][0]
        self.assertEqual(result["job_id"], item["job_id"])
        self.assertNotIn("classification", self.item())
        self.assertEqual(self.result(item["job_id"])["is_tech"], True)

    def test_duplicate_delivery_skips_second_paid_call(self):
        DynamoJobStore("jobs").save_jobs([self.job])
        body = {"job_id": self.item()["job_id"]}
        self.assertEqual(
            process_classify(body, self.jobs, self.store, self.classifier, "jev-latest"),
            "classified",
        )
        self.assertEqual(
            process_classify(body, self.jobs, self.store, self.classifier, "jev-latest"), "skipped"
        )
        self.classifier.assert_called_once()

    def test_active_lease_prevents_concurrent_provider_call(self):
        DynamoJobStore("jobs").save_jobs([self.job])
        item = self.item()
        self.results.put_item(
            Item={
                "job_id": item["job_id"],
                "lock_token": "another-worker",
                "lease_until": int(time.time()) + 300,
            }
        )
        with self.assertRaises(ClassificationBusy):
            self.store.classify(item, self.classifier)
        self.classifier.assert_not_called()

    def test_result_is_rechecked_after_acquiring_lease(self):
        from backend.jobs.classification.models import (
            SCHEMA_VERSION,
            classification_input,
            input_hash,
        )

        DynamoJobStore("jobs").save_jobs([self.job])
        item = self.item()
        original_update = self.results.update_item

        def update_with_completed_result(**kwargs):
            response = original_update(**kwargs)
            if "SET lock_token" in kwargs["UpdateExpression"]:
                original_update(
                    Key={"job_id": item["job_id"]},
                    UpdateExpression=(
                        "SET input_hash = :hash, model = :model, schema_version = :version, "
                        "#provider = :provider, seniority = :seniority, is_tech = :tech, "
                        "category = :category, contract_type = :contract"
                    ),
                    ExpressionAttributeNames={"#provider": "provider"},
                    ExpressionAttributeValues={
                        ":hash": input_hash(classification_input(item)),
                        ":model": "jev-latest",
                        ":version": SCHEMA_VERSION,
                        ":provider": "typesafe",
                        ":seniority": "senior",
                        ":tech": True,
                        ":category": "cloud_engineer",
                        ":contract": None,
                    },
                )
            return response

        with patch.object(self.results, "update_item", side_effect=update_with_completed_result):
            self.assertEqual(self.store.classify(item, self.classifier), "skipped")
        self.classifier.assert_not_called()

    def test_same_table_for_sources_and_results_is_rejected(self):
        with self.assertRaises(ValueError):
            ClassificationStore(self.jobs, self.jobs)

    def test_expired_lease_is_recoverable(self):
        DynamoJobStore("jobs").save_jobs([self.job])
        item = self.item()
        self.results.put_item(
            Item={
                "job_id": item["job_id"],
                "lock_token": "dead-worker",
                "lease_until": int(time.time()) - 1,
            }
        )
        self.assertEqual(self.store.classify(item, self.classifier), "classified")
        self.assertNotIn("lease_until", self.result(item["job_id"]))

    def test_failed_provider_releases_lease_and_does_not_ack_message(self):
        DynamoJobStore("jobs").save_jobs([self.job])
        job_id = self.item()["job_id"]
        message = self.message({"job_id": job_id})
        self.classifier.side_effect = RuntimeError("provider unavailable")
        with self.assertRaises(RuntimeError):
            handle_message(
                self.sqs,
                self.queue,
                message,
                lambda body: process_classify(
                    body, self.jobs, self.store, self.classifier, "jev-latest"
                ),
            )
        self.assertNotIn("lock_token", self.result(job_id))
        self.assertNotIn("category", self.result(job_id))
        self.sqs.change_message_visibility(
            QueueUrl=self.queue, ReceiptHandle=message["ReceiptHandle"], VisibilityTimeout=0
        )
        self.assertEqual(len(self.sqs.receive_message(QueueUrl=self.queue)["Messages"]), 1)

    def test_deleted_job_is_acknowledged_without_provider_call(self):
        self.assertEqual(
            process_classify(
                {"job_id": "deleted"}, self.jobs, self.store, self.classifier, "jev-latest"
            ),
            "deleted",
        )
        self.classifier.assert_not_called()

    def test_backfill_queues_job_ids_without_descriptions(self):
        DynamoJobStore("jobs").save_jobs([self.job])
        self.assertEqual(backfill(self.jobs, self.sqs, self.queue), 1)
        body = json.loads(self.sqs.receive_message(QueueUrl=self.queue)["Messages"][0]["Body"])
        self.assertEqual(body, {"job_id": self.item()["job_id"]})

    def test_unchanged_posting_does_not_schedule_more_classifications(self):
        serialize = TypeSerializer().serialize
        job = {"title": "Cloud Engineer", "description": "AWS"}
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                "OldImage": {
                    **{key: serialize(value) for key, value in job.items()},
                    "last_seen": {"S": "old"},
                },
                "NewImage": {
                    **{key: serialize(value) for key, value in job.items()},
                    "last_seen": {"S": "new"},
                },
            },
        }
        self.assertFalse(needs_classification(record))
        record["dynamodb"]["NewImage"]["title"] = serialize("Senior Cloud Engineer")
        self.assertTrue(needs_classification(record))
        record["eventName"] = "REMOVE"
        self.assertFalse(needs_classification(record))

    def test_router_reports_failed_sequence_for_stream_retry(self):
        record = {
            "eventName": "INSERT",
            "dynamodb": {
                "SequenceNumber": "42",
                "NewImage": {
                    "job_id": {"S": "a"},
                    "title": {"S": "Engineer"},
                },
            },
        }
        with patch("infra.stream_router.handler.boto3.client") as client:
            client.return_value.send_message.side_effect = RuntimeError("network unavailable")
            self.assertEqual(
                handler({"Records": [record]}, None),
                {"batchItemFailures": [{"itemIdentifier": "42"}]},
            )

    def test_flattening_legacy_posting_does_not_schedule_another_classification(self):
        serialize = TypeSerializer().serialize
        job = {"title": "Cloud Engineer", "description": "AWS"}
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                "OldImage": {"job": serialize(job)},
                "NewImage": {key: serialize(value) for key, value in job.items()},
            },
        }

        self.assertFalse(needs_classification(record))

        record["dynamodb"]["NewImage"]["description"] = serialize("Build AI tools")

        self.assertTrue(needs_classification(record))
