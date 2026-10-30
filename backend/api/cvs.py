"""LaTeX CVs stored as users/<user>/cvs/<cv_id>/<filename>.tex; the key holds all metadata."""

import re
import uuid
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Path, UploadFile
from pydantic import BaseModel

from backend.api.deps import AppContext, CurrentUser
from backend.documents.errors import DocumentError
from backend.documents.latex import validate_cv

router = APIRouter(prefix="/api/cvs", tags=["cvs"])

MAX_BYTES = 200_000  # GenerationInput.cv_latex limit
CvId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


class Cv(BaseModel):
    cv_id: str
    filename: str
    size: int
    uploaded_at: str


def prefix(user):
    return f"users/{quote(user, safe='')}/cvs/"


def summary(item):
    cv_id, filename = item["Key"].rsplit("/", 2)[1:]
    return Cv(
        cv_id=cv_id,
        filename=filename,
        size=item["Size"],
        uploaded_at=item["LastModified"].isoformat(),
    )


def find(context, user, cv_id):
    listing = context.s3.list_objects_v2(
        Bucket=context.settings.documents_bucket, Prefix=f"{prefix(user)}{cv_id}/"
    )
    items = listing.get("Contents", [])
    if not items:
        raise HTTPException(404, "CV not found")
    return items[0]


def read_cv(context, user, cv_id):
    key = find(context, user, cv_id)["Key"]
    response = context.s3.get_object(Bucket=context.settings.documents_bucket, Key=key)
    with response["Body"] as stream:
        return stream.read().decode("utf-8")


@router.get("")
def list_cvs(context: AppContext, user: CurrentUser) -> list[Cv]:
    paginator = context.s3.get_paginator("list_objects_v2")
    items = [
        item
        for page in paginator.paginate(
            Bucket=context.settings.documents_bucket, Prefix=prefix(user)
        )
        for item in page.get("Contents", [])
    ]
    cvs = [summary(item) for item in items]
    return sorted(cvs, key=lambda cv: cv.uploaded_at, reverse=True)


@router.post("", status_code=201)
def upload_cv(file: UploadFile, context: AppContext, user: CurrentUser) -> Cv:
    name = re.sub(r"[^\w.-]", "_", PurePosixPath((file.filename or "").replace("\\", "/")).name)
    if not name.lower().endswith(".tex"):
        raise HTTPException(400, "Upload a single .tex file")
    body = file.file.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise HTTPException(400, "This CV is larger than the 200 KB limit")
    try:
        validate_cv(body.decode("utf-8"))
    except UnicodeDecodeError:
        raise HTTPException(400, "This CV is not UTF-8 text") from None
    except DocumentError as exc:
        raise HTTPException(400, str(exc)) from None

    cv_id = uuid.uuid4().hex
    key = f"{prefix(user)}{cv_id}/{name}"
    context.s3.put_object(
        Bucket=context.settings.documents_bucket,
        Key=key,
        Body=body,
        ContentType="application/x-tex",
    )
    return summary(
        context.s3.list_objects_v2(Bucket=context.settings.documents_bucket, Prefix=key)[
            "Contents"
        ][0]
    )


@router.delete("/{cv_id}", status_code=204)
def delete_cv(cv_id: CvId, context: AppContext, user: CurrentUser) -> None:
    """Earlier generations keep working: each one froze its own copy of the CV."""
    context.s3.delete_object(
        Bucket=context.settings.documents_bucket, Key=find(context, user, cv_id)["Key"]
    )
