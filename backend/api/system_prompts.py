"""Named system prompts in one S3 file per user, so the list and its default change together."""

import json
import uuid
from typing import Annotated
from urllib.parse import quote

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Path

from backend.api.deps import AppContext, CurrentUser
from backend.documents.models import SystemPrompt

router = APIRouter(prefix="/api/system-prompts", tags=["system-prompts"])

PromptId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
CHANGED = "Your system prompts just changed. Try again."


class SystemPromptFields(SystemPrompt):
    is_default: bool = False  # Selected by standard when tailoring; at most one has this.


class SavedSystemPrompt(SystemPromptFields):
    prompt_id: str


def key(user):
    return f"users/{quote(user, safe='')}/system-prompts.json"


def read(context, user):
    """The saved prompts, and the file's ETag so that a write can notice a change in between."""
    try:
        response = context.s3.get_object(Bucket=context.settings.documents_bucket, Key=key(user))
    except context.s3.exceptions.NoSuchKey:
        return [], None
    with response["Body"] as stream:
        prompts = [SavedSystemPrompt.model_validate(item) for item in json.load(stream)]
    return prompts, response["ETag"]


def write(context, user, prompts, etag):
    try:
        context.s3.put_object(
            Bucket=context.settings.documents_bucket,
            Key=key(user),
            Body=json.dumps([prompt.model_dump() for prompt in prompts], ensure_ascii=False),
            ContentType="application/json",
            **({"IfMatch": etag} if etag else {"IfNoneMatch": "*"}),
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"PreconditionFailed", "ConditionalRequestConflict"}:
            raise HTTPException(409, CHANGED) from None
        raise


def find(prompts, prompt_id):
    for prompt in prompts:
        if prompt.prompt_id == prompt_id:
            return prompt
    raise HTTPException(404, "That system prompt no longer exists")


def save(context, user, fields, prompt_id=None):
    prompts, etag = read(context, user)
    if prompt_id:
        find(prompts, prompt_id)
    others = [prompt for prompt in prompts if prompt.prompt_id != prompt_id]
    if fields.name.casefold() in {prompt.name.casefold() for prompt in others}:
        raise HTTPException(409, "You already have a system prompt with that name")
    if fields.is_default:
        others = [prompt.model_copy(update={"is_default": False}) for prompt in others]
    saved = SavedSystemPrompt(**fields.model_dump(), prompt_id=prompt_id or uuid.uuid4().hex)
    write(context, user, [*others, saved], etag)
    return saved


def load_system_prompt(context, user, prompt_id):
    """The name and text a generation freezes, so later edits here do not change it."""
    prompt = find(read(context, user)[0], prompt_id)
    return SystemPrompt(name=prompt.name, text=prompt.text)


@router.get("")
def list_system_prompts(context: AppContext, user: CurrentUser) -> list[SavedSystemPrompt]:
    return sorted(read(context, user)[0], key=lambda prompt: prompt.name.casefold())


@router.post("", status_code=201)
def create_system_prompt(
    fields: SystemPromptFields, context: AppContext, user: CurrentUser
) -> SavedSystemPrompt:
    return save(context, user, fields)


@router.put("/{prompt_id}")
def update_system_prompt(
    prompt_id: PromptId, fields: SystemPromptFields, context: AppContext, user: CurrentUser
) -> SavedSystemPrompt:
    return save(context, user, fields, prompt_id)


@router.delete("/{prompt_id}", status_code=204)
def delete_system_prompt(prompt_id: PromptId, context: AppContext, user: CurrentUser) -> None:
    """Earlier generations keep working: each one froze its own copy of the text."""
    prompts, etag = read(context, user)
    removed = find(prompts, prompt_id)
    write(context, user, [prompt for prompt in prompts if prompt is not removed], etag)
