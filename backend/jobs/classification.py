"""Classify stored jobs without refetching boards or changing their source data."""

import hashlib
import json
from collections.abc import Callable
from typing import Literal

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
    results_table,
    provider: str = "typesafe",
    model: str = "jev-latest",
    limit: int = 100,
    force: bool = False,
    page_size: int = 100,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Backfill separate results from all scan pages, with a bounded call count."""
    from backend.jobs.results import ClassificationBusy, ClassificationStore, JobChanged

    if limit < 1 or page_size < 1:
        raise ValueError("limit and page_size must be positive")
    if provider != "typesafe":
        raise ValueError("Only the typesafe provider is supported")
    store = ClassificationStore(table, results_table)
    counts = {"scanned": 0, "classified": 0, "skipped": 0, "failed": 0, "conflicts": 0}
    attempts = 0
    scan_args = {"Limit": page_size}
    while True:
        page = table.scan(**scan_args)
        for item in page.get("Items", []):
            counts["scanned"] += 1
            try:
                outcome = store.classify(item, classify, model=model, force=force)
                counts[outcome] += 1
                if outcome == "classified":
                    attempts += 1
            except (ClassificationBusy, JobChanged):
                attempts += 1
                counts["conflicts"] += 1
                if progress:
                    progress("Job changed or another worker is classifying it; retry later")
            except (KeyError, TypeError, AttributeError, ValidationError, ValueError):
                attempts += 1
                counts["failed"] += 1
                if progress:
                    progress("Skipped an invalid job record or classification result")
            if attempts >= limit:
                return counts
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return counts
        scan_args["ExclusiveStartKey"] = cursor
