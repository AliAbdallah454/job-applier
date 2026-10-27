"""Persist normalized postings in an existing DynamoDB table."""

import hashlib
import json
from datetime import UTC, datetime

import boto3


class DynamoJobStore:
    """Upsert postings by provider/board/region/identity, retaining first observation."""

    def __init__(self, table_name: str, *, region: str | None = None) -> None:
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        # Fail before fetching if access or the table schema is incorrect.
        if self.table.key_schema != [{"AttributeName": "job_id", "KeyType": "HASH"}]:
            raise ValueError("DynamoDB table must have only a string partition key named job_id")
        if not any(
            item == {"AttributeName": "job_id", "AttributeType": "S"}
            for item in self.table.attribute_definitions
        ):
            raise ValueError("DynamoDB job_id partition key must be a string")

    def save_jobs(self, jobs: list[dict]) -> None:
        for job in jobs:
            identity = [
                job["source"],
                job["board"],
                job["region"],
                job.get("source_id") or job["posting_url"],
            ]
            job_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            now = datetime.now(UTC).isoformat()
            self.table.update_item(
                Key={"job_id": job_id},
                UpdateExpression=(
                    "SET #job = :job, first_seen = if_not_exists(first_seen, :now), "
                    "last_seen = :now"
                ),
                ExpressionAttributeNames={"#job": "job"},
                ExpressionAttributeValues={":job": job, ":now": now},
            )

    def save_report(self, report: dict) -> None:
        """Write only newly collected postings from cumulative collector snapshots."""
        count = getattr(self, "_saved_count", 0)
        self.save_jobs(report["jobs"][count:])
        self._saved_count = len(report["jobs"])
