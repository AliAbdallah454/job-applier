"""Frozen generation inputs and the model's proposed edits to the user's CV."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.profiles.models import CandidateProfile

# Part of the request hash: bumping it makes an unchanged CV/profile/job produce a new generation.
PROMPT_VERSION = 6
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
Snippet = Annotated[str, StringConstraints(max_length=5000)]


class StrictModel(BaseModel):
    """Base for document models: unknown fields are rejected, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


class Job(StrictModel):
    """The job being applied to, as given to the model."""

    job_id: Text
    title: Text
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100000)
    ]
    company: Text | None = None


class GenerationInput(StrictModel):
    """Everything one generation depends on; frozen in S3 when the request is submitted."""

    profile: CandidateProfile
    job: Job
    model_id: Text
    cv_latex: Annotated[str, StringConstraints(min_length=1, max_length=200000)]
    prompt_version: Literal[5, 6] = PROMPT_VERSION


class CvEdit(StrictModel):
    """Replace one exact snippet of the CV source; checked and applied by `latex.py`."""

    find: Snippet = Field(
        min_length=1,
        description="Exact snippet copied from the CV body, unique in the file, kept short",
    )
    replace: Snippet = Field(
        description="New LaTeX for that snippet, using only commands that appear in find; "
        "empty to delete it"
    )
    reason: Text = Field(description="Which job requirement this serves, in a few words")


class TailoredCv(StrictModel):
    """Edits that make the CV fit the job, plus the motivation letter."""

    # Not rendered: listing the keywords first steers the edits that follow.
    job_keywords: list[Text] = Field(
        description="8-15 key technologies, methods and terms in the posting, most important first"
    )
    edits: list[CvEdit] = Field(
        max_length=40, description="Non-overlapping edits to the CV, in any order"
    )
    letter_paragraphs: list[Text] = Field(
        min_length=2,
        max_length=6,
        description="3-4 letter body paragraphs; no greeting, sign-off, date or address",
    )
