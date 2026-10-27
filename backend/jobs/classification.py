"""Classify stored jobs without refetching boards or changing their source data."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

SCHEMA_VERSION = 1

Seniority = Literal[
    "intern",
    "apprentice",
    "entry",
    "junior",
    "mid",
    "senior",
    "lead",
    "staff",
    "principal",
    "manager",
    "director",
    "executive",
    "unknown",
]
JobCategory = Literal[
    "software_engineer",
    "devops_engineer",
    "cloud_engineer",
    "ai_engineer",
    "ml_engineer",
    "data_engineer",
    "data_scientist",
    "security_engineer",
    "qa_engineer",
    "systems_engineer",
    "technical_support",
    "technical_management",
    "other_tech",
    "non_technical",
    "unknown",
]


class JobClassification(BaseModel):
    """Strict output contract shared by the provider and stored results."""

    model_config = ConfigDict(extra="forbid", strict=True)

    seniority: Seniority
    is_tech: bool | None
    category: JobCategory

    @model_validator(mode="after")
    def consistent_category(self) -> "JobClassification":
        if self.category == "non_technical":
            if self.is_tech is not False:
                raise ValueError("non_technical requires is_tech=false")
        elif self.category == "unknown":
            if self.is_tech is not None:
                raise ValueError("unknown category requires is_tech=null")
        elif self.is_tech is not True:
            raise ValueError("Technical categories require is_tech=true")
        return self


def classification_input(job: dict) -> dict:
    """Only send the title and description needed to classify the role."""
    title = job.get("title")
    description = job.get("description", "")
    if not isinstance(title, str) or not title.strip() or not isinstance(description, str):
        raise ValueError("Job needs a non-empty title and a string description")
    return {"title": title, "description": description}


def input_hash(job_input: dict) -> str:
    return hashlib.sha256(
        json.dumps(job_input, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def classify_table(
    table,
    classify: Callable[[dict], JobClassification],
    *,
    provider: str,
    model: str,
    limit: int = 100,
    force: bool = False,
    page_size: int = 100,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Scan all pages, reusing current results and conditionally saving new ones.

    limit bounds classification attempts, not DynamoDB's scanned item count.
    Provider errors propagate to the caller; previously saved results remain.
    Invalid records/results are counted and processing continues. Concurrent
    source edits/deletions are never overwritten with stale classifications.
    """
    if limit < 1 or page_size < 1:
        raise ValueError("limit and page_size must be positive")
    counts = {"scanned": 0, "classified": 0, "skipped": 0, "failed": 0, "conflicts": 0}
    attempts = 0
    scan_args = {"Limit": page_size}
    while True:
        page = table.scan(**scan_args)
        for item in page.get("Items", []):
            counts["scanned"] += 1
            try:
                job = item["job"]
                job_input = classification_input(job)
                fingerprint = input_hash(job_input)
                previous = item.get("classification", {})
                if not force and (
                    previous.get("input_hash") == fingerprint
                    and previous.get("schema_version") == SCHEMA_VERSION
                    and previous.get("provider") == provider
                    and previous.get("model") == model
                ):
                    counts["skipped"] += 1
                    continue
                attempts += 1
                result = JobClassification.model_validate(classify(job_input))
                classification = {
                    **result.model_dump(),
                    "input_hash": fingerprint,
                    "schema_version": SCHEMA_VERSION,
                    "provider": provider,
                    "model": model,
                    "classified_at": datetime.now(UTC).isoformat(),
                }
                table.update_item(
                    Key={"job_id": item["job_id"]},
                    UpdateExpression="SET #classification = :classification",
                    ConditionExpression="attribute_exists(job_id) AND #job = :original",
                    ExpressionAttributeNames={"#classification": "classification", "#job": "job"},
                    ExpressionAttributeValues={
                        ":classification": classification,
                        ":original": job,
                    },
                )
                counts["classified"] += 1
            except (KeyError, TypeError, AttributeError, ValidationError, ValueError):
                counts["failed"] += 1
                if progress:
                    progress("Skipped an invalid job record or classification result")
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                counts["conflicts"] += 1
                if progress:
                    progress("Job changed or was deleted during classification; result discarded")
            if attempts >= limit:
                return counts
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return counts
        scan_args["ExclusiveStartKey"] = cursor
