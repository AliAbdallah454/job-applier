"""Classification schema and source fingerprints shared by providers and storage."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

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

    # @model_validator(mode="after")
    # def consistent_category(self) -> "JobClassification":

    #     if self.category == "non_technical":
    #         if self.is_tech is not False:
    #             raise ValueError("non_technical requires is_tech=false")
    #     elif self.category == "unknown":
    #         if self.is_tech is not None:
    #             raise ValueError("unknown category requires is_tech=null")
    #     elif self.is_tech is not True:
    #         raise ValueError("Technical categories require is_tech=true")

    #     return self


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
