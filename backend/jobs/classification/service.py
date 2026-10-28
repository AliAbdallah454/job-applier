"""Classify stored jobs without refetching boards or changing source postings."""

from collections.abc import Callable

from pydantic import ValidationError

from backend.jobs.classification.models import JobClassification
from backend.jobs.storage.classifications import ClassificationBusy, ClassificationStore, JobChanged


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
