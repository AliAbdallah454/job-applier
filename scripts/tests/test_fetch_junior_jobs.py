"""Offline filter checks against Moto tables."""

import unittest

import boto3
from moto import mock_aws

from backend.jobs.classification.models import classification_input, input_hash
from scripts.fetch_junior_jobs import junior_jobs


@mock_aws
class JuniorJobsTests(unittest.TestCase):
    def setUp(self):
        self.db = boto3.resource("dynamodb", region_name="us-east-1")
        jobs = self.db.create_table(
            TableName="jobs",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        self.results = self.db.create_table(
            TableName="classifications",
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
            BillingMode="PAY_PER_REQUEST",
        )
        self.jobs = jobs

    def add(self, job_id, seniority, contract_type, tech="tech", stale=False):
        posting = {"job_id": job_id, "title": f"Role {job_id}", "description": "Build things"}
        self.jobs.put_item(Item=posting)
        fingerprint = "old" if stale else input_hash(classification_input(posting))
        self.results.put_item(
            Item={
                "job_id": job_id,
                "seniority": seniority,
                "contract_type": contract_type,
                "category": "software_engineer",
                "is_tech": tech == "tech",
                "tech_status": tech,
                "seniority_job": f"{seniority}#{job_id}",
                "input_hash": fingerprint,
            }
        )

    def test_keeps_entry_and_junior_tech_jobs_that_are_not_internships(self):
        self.add("entry-permanent", "entry", "permanent")
        self.add("junior-unknown", "junior", None)
        self.add("junior-intern", "junior", "internship")
        self.add("mid", "mid", "permanent")
        self.add("non-tech", "junior", "permanent", tech="non_tech")
        self.add("stale", "entry", "permanent", stale=True)

        rows = junior_jobs(self.db, "jobs", "classifications")

        self.assertEqual(
            sorted(row["job_id"] for row in rows), ["entry-permanent", "junior-unknown"]
        )
        self.assertIn("unknown", [row["contract_type"] for row in rows])
