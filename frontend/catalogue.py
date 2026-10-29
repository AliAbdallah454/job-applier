"""Read paginated classified postings using the existing DynamoDB indexes."""

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import get_args

from boto3.dynamodb.conditions import Attr, Key

from backend.jobs.classification.models import (
    ContractType,
    JobCategory,
    JobClassification,
    Seniority,
    classification_input,
    input_hash,
)


@dataclass(frozen=True)
class JobFilters:
    tech: str = "tech"
    seniorities: tuple[str, ...] = ("entry", "junior")
    category: str = ""
    contract_type: str = ""
    search: str = ""

    def __post_init__(self):
        if self.tech not in {"tech", "non_tech", "unknown"}:
            raise ValueError("Invalid technical relevance")
        if not set(self.seniorities) <= set(get_args(Seniority)) | {"unknown"}:
            raise ValueError("Invalid seniority")
        if self.category and self.category not in get_args(JobCategory):
            raise ValueError("Invalid category")
        if self.contract_type and self.contract_type not in (*get_args(ContractType), "unknown"):
            raise ValueError("Invalid contract type")
        if len(self.search) > 200:
            raise ValueError("Search must be at most 200 characters")

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True).encode()).hexdigest()


def encode_cursor(key, filters):
    if not key:
        return None
    payload = json.dumps({"key": key, "filters": filters.fingerprint()}).encode()
    return base64.urlsafe_b64encode(payload).decode()


def decode_cursor(cursor, filters):
    try:
        if len(cursor) > 4096:
            raise ValueError
        payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        key = payload["key"]
        if payload["filters"] != filters.fingerprint():
            raise ValueError
        if set(key) != {"job_id", "tech_status", "seniority_job"}:
            raise ValueError
        if not all(isinstance(value, str) and value for value in key.values()):
            raise ValueError
        return key
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Invalid pagination cursor; refresh the search") from exc


class JobCatalogue:
    def __init__(self, dynamodb, jobs_table, classifications_table):
        self.db = dynamodb
        self.jobs_table = jobs_table
        self.results = dynamodb.Table(classifications_table)

    def page(self, filters: JobFilters, *, cursor=None, page_size=50):
        if not 1 <= page_size <= 100:
            raise ValueError("Page size must be between 1 and 100")

        arguments = {
            "IndexName": "ByTechSeniority",
            "KeyConditionExpression": Key("tech_status").eq(filters.tech),
            "Limit": page_size,
        }
        condition = None
        if filters.seniorities:
            condition = Attr("seniority").is_in(list(filters.seniorities))
            if "unknown" in filters.seniorities:
                condition = condition | Attr("seniority").eq(None)
        if filters.category:
            category = Attr("category").eq(filters.category)
            condition = category if condition is None else condition & category
        if filters.contract_type:
            contract = (
                Attr("contract_type").eq(filters.contract_type)
                if filters.contract_type != "unknown"
                else Attr("contract_type").eq(None) | Attr("contract_type").not_exists()
            )
            condition = contract if condition is None else condition & contract
        if condition is not None:
            arguments["FilterExpression"] = condition
        if cursor:
            arguments["ExclusiveStartKey"] = decode_cursor(cursor, filters)

        page = self.results.query(**arguments)
        results = page.get("Items", [])
        jobs = self._postings([item["job_id"] for item in results]) if results else {}
        matches = []

        for result in results:
            job = jobs.get(result["job_id"])
            if not job:
                continue
            posting = job.get("job", job) if "title" not in job else job
            try:
                state = classification_input(posting)
                if result.get("input_hash") != input_hash(state):
                    continue
                classification = JobClassification.model_validate(
                    {
                        "seniority": None
                        if result.get("seniority") == "unknown"
                        else result.get("seniority"),
                        "contract_type": result.get("contract_type"),
                        "category": result["category"],
                        "is_tech": result["is_tech"],
                    }
                )
            except (ValueError, KeyError):
                continue

            text = " ".join(
                str(posting.get(field) or "")
                for field in ("title", "company", "location", "description")
            )
            if not all(word in text.casefold() for word in filters.search.casefold().split()):
                continue

            matches.append(
                {
                    "job_id": result["job_id"],
                    **{
                        field: posting.get(field)
                        for field in (
                            "title",
                            "company",
                            "location",
                            "description",
                            "source",
                            "posting_url",
                            "application_url",
                            "published_at",
                            "published_text",
                        )
                    },
                    **classification.model_dump(),
                }
            )

        return {
            "jobs": matches,
            "next_cursor": encode_cursor(page.get("LastEvaluatedKey"), filters),
        }

    def _postings(self, job_ids):
        pending = {
            self.jobs_table: {
                "Keys": [{"job_id": job_id} for job_id in dict.fromkeys(job_ids)],
                "ConsistentRead": True,
            }
        }
        postings = {}

        for attempt in range(4):
            response = self.db.batch_get_item(RequestItems=pending)
            for item in response.get("Responses", {}).get(self.jobs_table, []):
                postings[item["job_id"]] = item
            pending = response.get("UnprocessedKeys", {})
            if not pending:
                return postings
            if attempt < 3:
                time.sleep(0.1 * 2**attempt)

        raise RuntimeError("DynamoDB could not read all postings; please retry")
