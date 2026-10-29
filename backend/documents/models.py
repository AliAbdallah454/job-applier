"""Frozen generation inputs and validated, source-linked model output."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.documents.errors import DocumentError
from backend.profiles.models import CandidateProfile

PROMPT_VERSION = 1
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]


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
    cv_template: Text
    prompt_version: Literal[1] = PROMPT_VERSION


class TailoredEntry(StrictModel):
    """A profile experience or project chosen for this job, with rewritten bullets."""

    source_id: Text
    bullets: list[Text] = Field(min_length=1, max_length=5)


class GeneratedContent(StrictModel):
    """The model's output: which profile facts to show and the text for the CV and letter."""

    headline: Text
    summary: Text
    experiences: list[TailoredEntry]
    projects: list[TailoredEntry]
    education_ids: list[Text]
    skill_names: list[Text]
    certification_ids: list[Text]
    letter_paragraphs: list[Text] = Field(min_length=2, max_length=6)
    letter_source_ids: list[Text]

    def validate_sources(self, profile: CandidateProfile):
        for field, selected, available in (
            (
                "experiences",
                [entry.source_id for entry in self.experiences],
                [entry.id for entry in profile.experiences],
            ),
            (
                "projects",
                [entry.source_id for entry in self.projects],
                [entry.id for entry in profile.projects],
            ),
            ("education_ids", self.education_ids, [entry.id for entry in profile.education]),
            (
                "certification_ids",
                self.certification_ids,
                [entry.id for entry in profile.certifications],
            ),
            ("skill_names", self.skill_names, [skill.name for skill in profile.skills]),
        ):
            if len(selected) != len(set(selected)) or not set(selected) <= set(available):
                raise DocumentError(
                    f"Generated {field} references duplicate or unsupported profile facts"
                )

        available_ids = {
            entry.id
            for entries in (
                profile.experiences,
                profile.projects,
                profile.education,
                profile.certifications,
            )
            for entry in entries
        }
        if not set(self.letter_source_ids) <= available_ids:
            raise DocumentError("Motivation letter references unknown profile entries")

        return self
