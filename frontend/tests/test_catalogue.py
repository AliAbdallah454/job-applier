"""Exercise real index queries and source joins without contacting AWS."""

import os
import unittest
from unittest.mock import patch

import boto3
from moto import mock_aws

from backend.jobs.classification.models import input_hash
from frontend.catalogue import JobCatalogue, JobFilters


@mock_aws
class CatalogueTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        self.db = boto3.resource("dynamodb", region_name="us-east-1")
        self.jobs = self.db.create_table(
            TableName="jobs",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
        )
        self.results = self.db.create_table(
            TableName="classifications",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("job_id", "tech_status", "seniority_job")
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "ByTechSeniority",
                    "KeySchema": [
                        {"AttributeName": "tech_status", "KeyType": "HASH"},
                        {"AttributeName": "seniority_job", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        self.catalogue = JobCatalogue(self.db, self.jobs.name, self.results.name)
        self.add("entry", "entry", "permanent")
        self.add("junior", "junior", "internship")
        self.add("mid", "mid", "fixed_term")
        self.add("senior", "senior", "freelance")
        self.add("unknown", None, None)
        self.add("non-tech", "entry", "permanent", tech=False)

    def add(self, job_id, seniority, contract, *, tech=True):
        state = {"title": f"Engineer {job_id}", "description": "Build Python services"}
        self.jobs.put_item(
            Item={
                "job_id": job_id,
                **state,
                "company": "Example",
                "location": "Paris",
                "source": "greenhouse",
                "posting_url": "https://example.com/jobs",
            }
        )
        self.results.put_item(
            Item={
                "job_id": job_id,
                "seniority": seniority,
                "contract_type": contract,
                "is_tech": tech,
                "category": "software_engineer" if tech else "non_technical",
                "tech_status": "tech" if tech else "non_tech",
                "seniority_job": f"{seniority or 'unknown'}#{job_id}",
                "input_hash": input_hash(state),
            }
        )

    def test_default_filters_only_return_tech_entry_and_junior(self):
        page = self.catalogue.page(JobFilters())
        self.assertEqual({job["job_id"] for job in page["jobs"]}, {"entry", "junior"})

    def test_category_contract_and_text_filters_combine(self):
        page = self.catalogue.page(
            JobFilters(
                category="software_engineer", contract_type="internship", search="Python Paris"
            )
        )
        self.assertEqual([job["job_id"] for job in page["jobs"]], ["junior"])
        self.assertEqual(self.catalogue.page(JobFilters(search="missing"))["jobs"], [])

    def test_non_tech_and_unspecified_filters(self):
        page = self.catalogue.page(JobFilters(tech="non_tech"))
        self.assertEqual([job["job_id"] for job in page["jobs"]], ["non-tech"])
        page = self.catalogue.page(JobFilters(seniorities=("unknown",), contract_type="unknown"))
        self.assertEqual([job["job_id"] for job in page["jobs"]], ["unknown"])

    def test_paginated_search_continues_through_empty_filtered_pages(self):
        filters = JobFilters(contract_type="internship")
        cursor = None
        found = []
        saw_empty = False
        for _ in range(10):
            page = self.catalogue.page(filters, cursor=cursor, page_size=1)
            found.extend(job["job_id"] for job in page["jobs"])
            cursor = page["next_cursor"]
            saw_empty |= not page["jobs"] and cursor is not None
            if not cursor:
                break
        else:
            self.fail("Pagination did not terminate")
        self.assertEqual(found, ["junior"])
        self.assertTrue(saw_empty)

    def test_orphaned_and_stale_results_are_not_displayed(self):
        self.jobs.delete_item(Key={"job_id": "entry"})
        self.jobs.update_item(
            Key={"job_id": "junior"},
            UpdateExpression="SET description = :new",
            ExpressionAttributeValues={":new": "Changed duties"},
        )
        self.assertEqual(self.catalogue.page(JobFilters())["jobs"], [])

    def test_invalid_cursor_and_cursor_for_other_filters_are_rejected(self):
        cursor = self.catalogue.page(JobFilters(), page_size=1)["next_cursor"]
        with self.assertRaises(ValueError):
            self.catalogue.page(JobFilters(seniorities=("senior",)), cursor=cursor)
        with self.assertRaises(ValueError):
            self.catalogue.page(JobFilters(), cursor="not a cursor")

    def test_unprocessed_batch_reads_are_retried_and_not_silently_dropped(self):
        real_read = self.db.batch_get_item
        calls = []

        def throttled(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"UnprocessedKeys": kwargs["RequestItems"]}
            return real_read(**kwargs)

        with (
            patch.object(self.db, "batch_get_item", side_effect=throttled),
            patch("frontend.catalogue.time.sleep"),
        ):
            self.assertEqual(len(self.catalogue.page(JobFilters())["jobs"]), 2)
        self.assertEqual(len(calls), 2)

        with (
            patch.object(
                self.db,
                "batch_get_item",
                side_effect=lambda **kwargs: {"UnprocessedKeys": kwargs["RequestItems"]},
            ),
            patch("frontend.catalogue.time.sleep"),
            self.assertRaises(RuntimeError),
        ):
            self.catalogue.page(JobFilters())

    def test_invalid_filters_fail_before_querying(self):
        for arguments in (
            {"tech": "invalid"},
            {"seniorities": ("lead",)},
            {"contract_type": "full_time"},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                JobFilters(**arguments)
