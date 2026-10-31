"""Frozen generation inputs and the model's rewrite of the user's CV."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.profiles.models import CandidateProfile

# Part of the request hash: bumping it makes an unchanged CV/profile/job produce a new generation.
PROMPT_VERSION = 9
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
Language = Literal["en", "fr"]


class StrictModel(BaseModel):
    """Base for document models: unknown fields are rejected, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


class ModelOption(StrictModel):
    """A model or language the user can pick for tailoring."""

    id: Text
    label: Text


# The first is the default. Only Claude and Nova Pro IDs are accepted, and each one is given
# IAM access by the Application stack, so a change here needs a deploy.
DOCUMENT_MODELS = (
    ModelOption(id="us.anthropic.claude-sonnet-4-6", label="Claude Sonnet 4.6"),
    ModelOption(id="us.anthropic.claude-opus-4-6-v1", label="Claude Opus 4.6"),
    ModelOption(id="us.amazon.nova-pro-v1:0", label="Nova Pro"),
)


LANGUAGES = (ModelOption(id="en", label="English"), ModelOption(id="fr", label="French"))


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
    prompt_version: Literal[5, 6, 7, 8, 9] = PROMPT_VERSION
    language: Language = "en"  # Of the CV and letters; the change list is always in English.


class CvChange(StrictModel):
    """The model's own account of one change, shown to the user beside the computed diff."""

    change: Text = Field(description="What changed in the CV, in a few words")
    reason: Text = Field(description="Which job requirement this serves, in a few words")


class TailoredCv(StrictModel):
    """The rewritten CV body, what changed in it, and the two letters."""

    # Not rendered: listing the keywords first steers the rewrite that follows.
    job_keywords: list[Text] = Field(
        description="8-15 key technologies, methods and terms in the posting, most important first"
    )
    cv_body: Annotated[str, StringConstraints(min_length=1, max_length=200000)] = Field(
        description="The complete LaTeX between \\begin{document} and \\end{document}, rewritten"
    )
    changes: list[CvChange] = Field(
        max_length=40, description="Each change made to the CV and the job requirement it serves"
    )
    motivation_letter_paragraphs: list[Text] = Field(
        min_length=2,
        max_length=6,
        description="3-4 body paragraphs on why the candidate wants this role; "
        "no greeting, sign-off, date or address",
    )
    cover_letter_paragraphs: list[Text] = Field(
        min_length=2,
        max_length=6,
        description="3 body paragraphs on the evidence that the candidate fits this role; "
        "no greeting, sign-off, date or address",
    )
