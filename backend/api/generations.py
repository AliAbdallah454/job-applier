"""Start tailoring runs and read their status, review list and files."""

import json
import time
from pathlib import PurePosixPath
from typing import Annotated, Literal

from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ValidationError

from backend.api.cvs import read_cv
from backend.api.deps import AppContext, CurrentUser
from backend.api.profile import load_profile
from backend.documents.models import PROMPT_VERSION, GenerationInput, Job

router = APIRouter(prefix="/api/generations", tags=["generations"])

Status = Literal["queued", "processing", "ready", "failed"]
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".tex": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
}


class GenerationRequest(BaseModel):
    job_id: str
    cv_id: str
    model_id: str | None = None  # The first configured model when left out.


class Change(BaseModel):
    """One difference computed from the original and tailored CV sources."""

    before: str
    after: str


class Explanation(BaseModel):
    """The model's account of a change; it can be incomplete, unlike the computed diff."""

    change: str
    reason: str


class Generation(BaseModel):
    generation_id: str
    job_id: str
    job_title: str
    model_id: str
    status: Status
    created_at: str
    updated_at: str
    review_required: bool


class GenerationDetail(Generation):
    # A worker that died mid-run leaves the record processing with an expired lease.
    retryable: bool
    failure_stage: str | None = None
    error_detail: str | None = None
    keywords: list[str]
    changes: list[Change]
    explanations: list[Explanation]
    files: list[str]
    compilation_log: str | None = None


def summary(record):
    return Generation.model_validate(record).model_dump()


def retryable(record):
    expired = record["status"] == "processing" and record.get("lease_until", 0) < time.time()
    return record["status"] == "failed" or expired


def downloads(record):
    ready = record.get("artifacts", {}) if record["status"] == "ready" else {}
    return {**ready, **record.get("failure_artifacts", {})}


def read_json(context, reference):
    return json.loads(context.generations.read_file(reference))


def detail(context, record):
    changes, explanations, keywords, log = [], [], [], None
    files = downloads(record)
    if "changes_json" in files:
        saved = read_json(context, files["changes_json"])
        # Generations from before prompt version 7 stored a list of find/replace edits.
        if isinstance(saved, dict):
            changes, explanations = saved["diff"], saved["explanations"]
    if record["status"] == "ready" and "content" in record:
        keywords = read_json(context, record["content"])["content"]["job_keywords"]
    if "compilation_log" in files:
        log = context.generations.read_file(files["compilation_log"]).decode("utf-8", "replace")
    return GenerationDetail(
        **summary(record),
        retryable=retryable(record),
        failure_stage=record.get("failure_stage"),
        error_detail=record.get("error_detail"),
        keywords=keywords,
        changes=changes,
        explanations=explanations,
        files=sorted(files),
        compilation_log=log,
    )


def load(context, user, generation_id):
    record = context.generations.get(user, generation_id)
    if record is None:
        raise HTTPException(404, "Generation not found")
    return record


def load_job(context, job_id):
    item = context.jobs.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if item is None:
        raise HTTPException(404, "Job not found")
    posting = item.get("job", item) if "title" not in item else item
    fields = {key: posting[key] for key in ("title", "description", "company") if key in posting}
    try:
        return Job(job_id=job_id, **fields)
    except ValidationError:
        raise HTTPException(422, "This job has no usable title or description") from None


def model_ids(context):
    return [model["id"] for model in context.settings.document_models]


def submit(context, request, *, force=False):
    settings = context.settings
    return context.generations.submit(
        request, context.client("sqs"), settings.document_queue_url, force=force
    )


@router.post("", status_code=201)
def create_generation(
    body: GenerationRequest, context: AppContext, user: CurrentUser
) -> Generation:
    profile = load_profile(context, user)
    if profile is None:
        raise HTTPException(409, "Fill in your profile before tailoring a CV")
    models = model_ids(context)
    if body.model_id and body.model_id not in models:
        raise HTTPException(422, "Unknown model")
    request = GenerationInput(
        profile=profile,
        job=load_job(context, body.job_id),
        model_id=body.model_id or models[0],
        cv_latex=read_cv(context, user, body.cv_id),
    )
    return summary(submit(context, request))


@router.get("")
def list_generations(
    context: AppContext,
    user: CurrentUser,
    job_id: str | None = None,
    status: Annotated[Status | None, Query()] = None,
) -> list[Generation]:
    table = context.generations.table
    arguments = {"KeyConditionExpression": Key("user_id").eq(user)}
    if job_id:
        arguments = {
            "IndexName": "ByUserJob",
            "KeyConditionExpression": Key("user_id").eq(user)
            & Key("job_created").begins_with(f"{job_id}#"),
        }
    records = []
    while True:
        page = table.query(**arguments)
        records.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        arguments["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    matches = [summary(item) for item in records if status in (None, item["status"])]
    return sorted(matches, key=lambda item: item["created_at"], reverse=True)


@router.get("/{generation_id}")
def get_generation(generation_id: str, context: AppContext, user: CurrentUser) -> GenerationDetail:
    return detail(context, load(context, user, generation_id))


@router.post("/{generation_id}/retry", status_code=202)
def retry_generation(generation_id: str, context: AppContext, user: CurrentUser) -> Generation:
    record = load(context, user, generation_id)
    if not retryable(record):
        raise HTTPException(409, f"A {record['status']} generation cannot be retried")
    settings = context.settings
    context.generations.enqueue(
        context.client("sqs"), settings.document_queue_url, user, generation_id
    )
    return summary(record)


@router.post("/{generation_id}/regenerate", status_code=201)
def regenerate(generation_id: str, context: AppContext, user: CurrentUser) -> Generation:
    """A new revision from the same frozen CV, profile and job."""
    record = load(context, user, generation_id)
    frozen = GenerationInput.model_validate_json(context.generations.read_file(record["input"]))
    models = model_ids(context)
    request = frozen.model_copy(
        update={
            # The same model again, unless it has since been removed from the config.
            "model_id": frozen.model_id if frozen.model_id in models else models[0],
            "prompt_version": PROMPT_VERSION,
        }
    )
    return summary(submit(context, request, force=True))


@router.get("/{generation_id}/files/{name}")
def get_file(
    generation_id: str,
    name: str,
    context: AppContext,
    user: CurrentUser,
    download: bool = False,
) -> Response:
    """Served by the API so the browser never needs a signed S3 link that can expire."""
    reference = downloads(load(context, user, generation_id)).get(name)
    if reference is None:
        raise HTTPException(404, "File not found")
    path = PurePosixPath(reference["key"])
    disposition = "attachment" if download else "inline"
    return Response(
        context.generations.read_file(reference),
        media_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
        headers={
            "Content-Disposition": f'{disposition}; filename="{path.name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )
