"""Start tailoring runs and read their status, review list and files."""

import json
import re
import time
from pathlib import PurePosixPath
from typing import Annotated, Literal

from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, StringConstraints, ValidationError

from backend.api.catalogue import posting_of
from backend.api.cvs import read_cv
from backend.api.deps import AppContext, CurrentUser
from backend.api.profile import load_profile
from backend.documents.errors import DocumentCompilationError
from backend.documents.latex import validate_cv
from backend.documents.models import PROMPT_VERSION, GenerationInput, Job
from backend.documents.render import compile_source

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


class CvEdit(BaseModel):
    cv_latex: Annotated[str, StringConstraints(min_length=1, max_length=200000)]


class Generation(BaseModel):
    generation_id: str
    job_id: str
    job_title: str
    company: str | None = None
    job_url: str | None = None
    model_id: str
    edited_from: str | None = None  # The generation whose CV the user edited.
    status: Status
    created_at: str
    updated_at: str
    review_required: bool
    files: list[str]


class GenerationDetail(Generation):
    # A worker that died mid-run leaves the record processing with an expired lease.
    retryable: bool
    failure_stage: str | None = None
    error_detail: str | None = None
    keywords: list[str]
    changes: list[Change]
    explanations: list[Explanation]
    compilation_log: str | None = None


def retryable(record):
    expired = record["status"] == "processing" and record.get("lease_until", 0) < time.time()
    return record["status"] == "failed" or expired


def downloads(record):
    ready = record.get("artifacts", {}) if record["status"] == "ready" else {}
    return {**ready, **record.get("failure_artifacts", {})}


def summaries(context, records):
    links = context.catalogue.links(record["job_id"] for record in records)
    return [
        Generation.model_validate(
            {**record, **links.get(record["job_id"], {}), "files": sorted(downloads(record))}
        ).model_dump()
        for record in records
    ]


def summary(context, record):
    return summaries(context, [record])[0]


def download_name(record, path):
    """Every generation's file is cv.pdf, so name downloads after the job to tell them apart."""
    title = re.sub(r"[^a-z0-9]+", "-", record["job_title"].lower()).strip("-")[:60]
    return f"{title}-{path.name}" if title else path.name


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
        **summary(context, record),
        retryable=retryable(record),
        failure_stage=record.get("failure_stage"),
        error_detail=record.get("error_detail"),
        keywords=keywords,
        changes=changes,
        explanations=explanations,
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
    posting = posting_of(item)
    fields = {key: posting[key] for key in ("title", "description", "company") if key in posting}
    try:
        return Job(job_id=job_id, **fields)
    except ValidationError:
        raise HTTPException(422, "This job has no usable title or description") from None


def model_ids(context):
    return [model.id for model in context.settings.document_models]


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
    return summary(context, submit(context, request))


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
    matches = [item for item in records if status in (None, item["status"])]
    return sorted(summaries(context, matches), key=lambda item: item["created_at"], reverse=True)


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
    return summary(context, record)


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
    return summary(context, submit(context, request, force=True))


@router.post("/{generation_id}/edit", status_code=201)
def edit_cv(
    generation_id: str, body: CvEdit, context: AppContext, user: CurrentUser
) -> GenerationDetail:
    """Compile the user's CV source here and save it as a new version; nothing is queued."""
    record = load(context, user, generation_id)
    if record["status"] != "ready":
        raise HTTPException(409, f"A {record['status']} generation cannot be edited")
    validate_cv(body.cv_latex)
    try:
        pdf = compile_source(body.cv_latex, context.settings.compile_document)
    except DocumentCompilationError as exc:
        # The compiler's output quotes this user's own source, and only goes back to them.
        raise HTTPException(422, exc.feedback) from None
    except RuntimeError as exc:  # TeX is not installed where the API runs.
        raise HTTPException(503, str(exc)) from None
    return detail(context, context.generations.save_edit(record, body.cv_latex, pdf))


@router.get("/{generation_id}/files/{name}")
def get_file(
    generation_id: str,
    name: str,
    context: AppContext,
    user: CurrentUser,
    download: bool = False,
) -> Response:
    """Served by the API so the browser never needs a signed S3 link that can expire."""
    record = load(context, user, generation_id)
    reference = downloads(record).get(name)
    if reference is None:
        raise HTTPException(404, "File not found")
    path = PurePosixPath(reference["key"])
    disposition = f'attachment; filename="{download_name(record, path)}"'
    if not download:
        disposition = f'inline; filename="{path.name}"'
    return Response(
        context.generations.read_file(reference),
        media_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )
