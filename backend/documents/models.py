"""Frozen generation inputs and validated, source-linked model output."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.documents.errors import DocumentError
from backend.profiles.models import CandidateProfile

# Part of the request hash: bumping it makes an unchanged profile/job produce a new generation.
PROMPT_VERSION = 3
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
    prompt_version: Literal[1, 2, 3] = PROMPT_VERSION


class TailoredEntry(StrictModel):
    """A profile experience or project chosen for this job, with rewritten bullets."""

    source_id: Text = Field(description="ID of the experience or project in the candidate facts")
    bullets: list[Text] = Field(
        min_length=1,
        max_length=5,
        description="One-line achievement bullets, most important for this job first",
    )


class GeneratedContent(StrictModel):
    """The model's output: which profile facts to show and the text for the CV and letter."""

    # Not rendered: listing the keywords first steers the selection and wording that follow.
    job_keywords: list[Text] = Field(
        description="8-15 key technologies, methods and terms in the posting, most important first"
    )
    headline: Text = Field(description="Professional identity aimed at this role, under 10 words")
    summary: Text = Field(description="2-3 sentences, 40-70 words, no pronouns or clichés")
    experiences: list[TailoredEntry] = Field(description="Relevant experiences, most recent first")
    projects: list[TailoredEntry] = Field(
        description="Projects matching a job keyword, at least 2 when available, up to 4"
    )
    education_ids: list[Text] = Field(description="Education entry IDs, most recent first")
    skill_names: list[Text] = Field(
        description="10-20 exact skill or technology names from the facts, job keywords first"
    )
    certification_ids: list[Text] = Field(description="IDs of relevant certifications only")
    letter_paragraphs: list[Text] = Field(
        min_length=2,
        max_length=6,
        description="3-4 letter body paragraphs; no greeting, sign-off, date or address",
    )
    letter_source_ids: list[Text] = Field(
        description="IDs of every experience, project, education or certification the letter uses"
    )

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
            (
                "skill_names",
                self.skill_names,
                [skill.name for skill in profile.skills]
                + [
                    technology
                    for entry in (*profile.experiences, *profile.projects)
                    for technology in entry.technologies
                ],
            ),
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
