"""Separate classification records with leases and atomic source-version checks."""

import uuid
from datetime import UTC, datetime, timedelta

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from backend.jobs.classification.models import (
    SCHEMA_VERSION,
    JobClassification,
    classification_input,
    input_hash,
)


class ClassificationBusy(RuntimeError):
    """Another worker holds a lease; leave the queue message for retry."""


class JobChanged(RuntimeError):
    """The source changed/deleted during the call; retry against current data."""


class ClassificationStore:
    def __init__(self, jobs, results):
        if jobs.name == results.name:
            raise ValueError("Jobs and classifications must use different tables")

        self.jobs = jobs
        self.results = results
        self.client = boto3.client("dynamodb", region_name=jobs.meta.client.meta.region_name)

    def classify(self, item, classifier, *, model="jev-latest", force=False):
        job_id = item["job_id"]
        # Legacy records remain readable until the next scrape flattens them.
        legacy = "title" not in item and isinstance(item.get("job"), dict)
        job = item["job"] if legacy else item

        state = classification_input(job)
        fingerprint = input_hash(state)

        def current_result():
            previous = self.results.get_item(Key={"job_id": job_id}, ConsistentRead=True).get(
                "Item", {}
            )
            if force or not all(
                previous.get(key) == value
                for key, value in {
                    "input_hash": fingerprint,
                    "model": model,
                    "schema_version": SCHEMA_VERSION,
                    "provider": "typesafe",
                }.items()
            ):
                return False

            try:
                JobClassification.model_validate(
                    {
                        key: previous[key]
                        for key in ("seniority", "category", "contract_type", "is_tech")
                    }
                )
            except (KeyError, ValueError):
                return False

            return True

        if current_result():
            return "skipped"

        token = str(uuid.uuid4())
        now = datetime.now(UTC)

        try:
            self.results.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET lock_token = :token, lease_until = :until",
                ConditionExpression="attribute_not_exists(lease_until) OR lease_until < :now",
                ExpressionAttributeValues={
                    ":token": token,
                    ":until": int((now + timedelta(minutes=5)).timestamp()),
                    ":now": int(now.timestamp()),
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ClassificationBusy("Another worker is classifying this job") from None

            raise

        try:
            # A competing worker may have completed between the first read and our lease.
            if current_result():
                return "skipped"

            result = JobClassification.model_validate(classifier(state))
            result_item = {
                "job_id": job_id,
                **result.model_dump(),
                "provider": "typesafe",
                "model": model,
                "schema_version": SCHEMA_VERSION,
                "input_hash": fingerprint,
                "classified_at": datetime.now(UTC).isoformat(),
                "tech_status": {True: "tech", False: "non_tech", None: "unknown"}[result.is_tech],
                "seniority_job": f"{result.seniority or 'unknown'}#{job_id}",
            }
            serialize = TypeSerializer().serialize

            if legacy:
                source_condition = "attribute_exists(job_id) AND #job = :job"
                source_names = {"#job": "job"}
                source_values = {":job": serialize(job)}

            else:
                source_condition = "attribute_exists(job_id) AND #title = :title"
                source_names = {"#title": "title", "#description": "description"}
                source_values = {":title": serialize(state["title"])}

                if "description" in item:
                    source_condition += " AND #description = :description"
                    source_values[":description"] = serialize(state["description"])

                else:
                    source_condition += " AND attribute_not_exists(#description)"

            try:
                self.client.transact_write_items(
                    TransactItems=[
                        {
                            "ConditionCheck": {
                                "TableName": self.jobs.name,
                                "Key": {"job_id": serialize(job_id)},
                                "ConditionExpression": source_condition,
                                "ExpressionAttributeNames": source_names,
                                "ExpressionAttributeValues": source_values,
                            }
                        },
                        {
                            "Put": {
                                "TableName": self.results.name,
                                "Item": {
                                    key: serialize(value) for key, value in result_item.items()
                                },
                                "ConditionExpression": "lock_token = :token",
                                "ExpressionAttributeValues": {":token": serialize(token)},
                            }
                        },
                    ]
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "TransactionCanceledException":
                    reasons = exc.response.get("CancellationReasons", [])
                    if any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons):
                        raise JobChanged("Source or classification lease changed") from None
                raise

            return "classified"
        finally:
            # Do not remove another worker's lease, or create a phantom result after success.
            try:
                self.results.update_item(
                    Key={"job_id": job_id},
                    UpdateExpression="REMOVE lock_token, lease_until",
                    ConditionExpression="lock_token = :token",
                    ExpressionAttributeValues={":token": token},
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
