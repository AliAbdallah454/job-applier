"""Start tailoring runs and read their status, review list and files."""

import json
import re
import time
from pathlib import PurePosixPath
from typing import Annotated, Literal, get_args

from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, StringConstraints, ValidationError

from backend.api.catalogue import posting_of
from backend.api.cvs import read_cv
from backend.api.deps import AppContext, CurrentUser
from backend.api.profile import load_profile
from backend.documents.errors import DocumentCompilationError
from backend.documents.latex import validate_cv
from backend.documents.models import PROMPT_VERSION, GenerationInput, Job, Language
from backend.documents.render import compile_source
from backend.mail.linkedin import job_url
from backend.mail.storage import MAIL_JOB_PREFIX

router = APIRouter(prefix="/api/generations", tags=["generations"])

Status = Literal["queued", "processing", "ready", "failed"]
Document = Literal["cv", "motivation_letter", "cover_letter"]
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".tex": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
}
CHANGED_ELSEWHERE = "This document was changed elsewhere. Save again to replace it."


class GenerationRequest(BaseModel):
    job_id: str
    cv_id: str
    model_id: str | None = None  # The first configured model when left out.
    language: Language = "en"


class Change(BaseModel):
    """One difference computed from the original and tailored CV sources."""

    before: str
    after: str


class Explanation(BaseModel):
    """The model's account of a change; it can be incomplete, unlike the computed diff."""

    change: str
    reason: str


class DocumentEdit(BaseModel):
    document: Document
    latex: Annotated[str, StringConstraints(min_length=1, max_length=200000)]
    revision: int  # The document's saved state the draft started from.


class DocumentVersion(BaseModel):
    """A CV or letter that a later edit replaced; number 1 is that document's oldest."""

    document: Document
    number: int
    saved_at: str


class SourceLine(BaseModel):
    line: int


class Generation(BaseModel):
    generation_id: str
    job_id: str
    job_title: str
    company: str | None = None
    job_url: str | None = None
    model_id: str
    language: Language = "en"  # Records from before languages have no value.
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
    previous_versions: list[DocumentVersion]
    # Each document's own count of saves, so saving one leaves drafts of the others valid.
    revisions: dict[str, int]
    # The old edit flow gave each edit its own record, so the first CV on one is the user's.
    starts_from_edit: bool


def retryable(record):
    expired = record["status"] == "processing" and record.get("lease_until", 0) < time.time()
    return record["status"] == "failed" or expired


def downloads(record):
    ready = record.get("artifacts", {}) if record["status"] == "ready" else {}
    return {**ready, **record.get("failure_artifacts", {})}


def revisions(record):
    """The documents this generation has; older ones have no cover letter."""
    return {
        document: len(record.get(f"{document}_versions", []))
        for document in get_args(Document)
        if f"{document}_tex" in downloads(record)
    }


def mail_job_links(context, user_id, job_ids):
    """Jobs from alert emails are not in the catalogue, so their links come from the mail table."""
    wanted = [job_id for job_id in job_ids if job_id.startswith(MAIL_JOB_PREFIX)]
    if not wanted or context.mail is None:
        return {}
    saved = context.mail.jobs(user_id)
    links = {}
    for job_id in wanted:
        linkedin_id = job_id.removeprefix(MAIL_JOB_PREFIX)
        if linkedin_id in saved:
            company = saved[linkedin_id].get("company") or None
            links[job_id] = {"company": company, "job_url": job_url(linkedin_id)}
    return links


def summaries(context, records):
    job_ids = [record["job_id"] for record in records]
    links = context.catalogue.links(job_ids)
    if records:
        links |= mail_job_links(context, records[0]["user_id"], job_ids)
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
        previous_versions=[
            DocumentVersion(document=document, number=number, saved_at=version["saved_at"])
            for document in get_args(Document)
            for number, version in enumerate(record.get(f"{document}_versions", []), start=1)
        ],
        revisions=revisions(record),
        starts_from_edit="edited_from" in record,
    )


def load(context, user, generation_id):
    record = context.generations.get(user, generation_id)
    if record is None:
        raise HTTPException(404, "Generation not found")
    return record


def load_mail_job(context, user, job_id):
    linkedin_id = job_id.removeprefix(MAIL_JOB_PREFIX)
    job = context.mail.job(user, linkedin_id) if context.mail else None
    if job is None:
        raise HTTPException(404, "Job not found")
    if not job.get("description"):
        raise HTTPException(409, "This job needs its description before tailoring")
    fields = {key: job[key] for key in ("title", "description", "company") if job.get(key)}
    try:
        return Job(job_id=job_id, **fields)
    except ValidationError:
        raise HTTPException(422, "This job has no usable title or description") from None


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


def job_for(context, user, job_id):
    if job_id.startswith(MAIL_JOB_PREFIX):
        return load_mail_job(context, user, job_id)
    return load_job(context, job_id)


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
        job=job_for(context, user, body.job_id),
        model_id=body.model_id or models[0],
        language=body.language,
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


@router.post("/{generation_id}/edit")
def edit_document(
    generation_id: str, body: DocumentEdit, context: AppContext, user: CurrentUser
) -> GenerationDetail:
    """Compile the user's source here and save it over the current document; nothing is queued."""
    record = load(context, user, generation_id)
    if record["status"] != "ready":
        raise HTTPException(409, f"A {record['status']} generation cannot be edited")
    saved_revisions = revisions(record)
    if body.document not in saved_revisions:
        raise HTTPException(404, "This generation does not have that document")
    if saved_revisions[body.document] != body.revision:
        raise HTTPException(409, CHANGED_ELSEWHERE)
    validate_cv(body.latex)
    try:
        pdf, synctex = compile_source(body.latex, context.settings.compile_document)
    except DocumentCompilationError as exc:
        # The compiler's output quotes this user's own source, and only goes back to them.
        raise HTTPException(422, exc.feedback) from None
    except RuntimeError as exc:  # TeX is not installed where the API runs.
        raise HTTPException(503, str(exc)) from None
    saved = context.generations.save_edit(record, body.latex, pdf, synctex, document=body.document)
    if saved is None:
        raise HTTPException(409, CHANGED_ELSEWHERE)
    return detail(context, saved)


@router.get("/{generation_id}/source-line")
def find_source_line(
    generation_id: str,
    page: Annotated[int, Query(ge=1)],
    x: Annotated[float, Query(ge=0)],
    y: Annotated[float, Query(ge=0)],
    context: AppContext,
    user: CurrentUser,
) -> SourceLine:
    """The line of the CV source at a position in its PDF, in points from the top left."""
    reference = downloads(load(context, user, generation_id)).get("cv_synctex")
    if reference is None:
        raise HTTPException(404, "This CV has no position data yet. Save an edit to add it.")
    synctex = context.generations.read_file(reference)
    try:
        line = context.settings.find_source_line(synctex, page, x, y)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
    if line is None:
        raise HTTPException(404, "No LaTeX source was found at that spot.")
    return SourceLine(line=line)


def file_response(context, record, reference, *, download, cache_control):
    """Served by the API so the browser never needs a signed S3 link that can expire."""
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
            "Cache-Control": cache_control,
        },
    )


@router.get("/{generation_id}/files/{name}")
def get_file(
    generation_id: str,
    name: str,
    context: AppContext,
    user: CurrentUser,
    download: bool = False,
) -> Response:
    """The current file. An edit changes what this URL returns, so it must be revalidated."""
    record = load(context, user, generation_id)
    reference = downloads(record).get(name)
    if reference is None:
        raise HTTPException(404, "File not found")
    return file_response(
        context, record, reference, download=download, cache_control="private, no-cache"
    )


@router.get("/{generation_id}/versions/{document}/{number}/files/{name}")
def get_version_file(
    generation_id: str,
    document: Document,
    number: int,
    name: str,
    context: AppContext,
    user: CurrentUser,
    download: bool = False,
) -> Response:
    """A file of a document that an edit replaced; those never change, so it can be cached."""
    record = load(context, user, generation_id)
    versions = record.get(f"{document}_versions", [])
    # Numbers start at 1, and without the check a negative one would count from the end.
    files = versions[number - 1]["files"] if 1 <= number <= len(versions) else {}
    if name not in files:
        raise HTTPException(404, "File not found")
    return file_response(
        context, record, files[name], download=download, cache_control="private, max-age=3600"
    )
