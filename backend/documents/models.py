"""Frozen generation inputs, the parsed CV and the model's validated edits to it."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.documents.errors import DocumentError
from backend.profiles.models import CandidateProfile

# Part of the request hash: bumping it makes an unchanged CV/profile/job produce a new generation.
PROMPT_VERSION = 4
NEW_BULLET = "new"
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
    cv_latex: Annotated[str, StringConstraints(min_length=1, max_length=200000)]
    prompt_version: Literal[4] = PROMPT_VERSION


# The parsed CV, independent of its file format. IDs are positional and stable per source.


class CvBullet(StrictModel):
    id: str
    text: str


class CvEntry(StrictModel):
    id: str
    title: str
    bullets: list[CvBullet]


class CvSkillGroup(StrictModel):
    id: str
    name: str
    skills: list[str]


class CvSection(StrictModel):
    id: str
    name: str
    entries: list[CvEntry]
    skill_groups: list[CvSkillGroup]


class CvDocument(StrictModel):
    sections: list[CvSection]


# The model's output: structured edits that the format adapter applies to the original CV.


class BulletEdit(StrictModel):
    source_id: Text = Field(
        description=f"ID of the CV bullet this is based on, or '{NEW_BULLET}' for a bullet "
        "built from the candidate facts"
    )
    text: Text = Field(description="Bullet text; repeat the original exactly to keep it as is")


class EntryEdit(StrictModel):
    entry_id: Text = Field(description="ID of the CV entry")
    bullets: list[BulletEdit] = Field(
        max_length=6, description="The entry's bullets in their new order, most relevant first"
    )


class SectionEdit(StrictModel):
    section_id: Text = Field(description="ID of a CV section that has entries")
    entries: list[EntryEdit] = Field(
        min_length=1, description="Entries to keep, in their new order; omitted ones are dropped"
    )


class SkillGroupEdit(StrictModel):
    group_id: Text = Field(description="ID of a CV skill group")
    skills: list[Text] = Field(
        min_length=1, description="Skills in their new order, job keywords first"
    )


class TailoredCv(StrictModel):
    """Edits that make the CV fit the job, plus the motivation letter."""

    # Not rendered: listing the keywords first steers the edits that follow.
    job_keywords: list[Text] = Field(
        description="8-15 key technologies, methods and terms in the posting, most important first"
    )
    sections: list[SectionEdit] = Field(
        description="Edited sections with entries; sections left out stay unchanged"
    )
    skill_groups: list[SkillGroupEdit] = Field(
        description="Edited skill groups; groups left out stay unchanged"
    )
    letter_paragraphs: list[Text] = Field(
        min_length=2,
        max_length=6,
        description="3-4 letter body paragraphs; no greeting, sign-off, date or address",
    )

    def validate_against(self, cv: CvDocument, profile: CandidateProfile):
        sections = {section.id: section for section in cv.sections}
        groups = {group.id: group for section in cv.sections for group in section.skill_groups}
        known_skills = {skill.casefold() for group in groups.values() for skill in group.skills}
        known_skills |= {skill.name.casefold() for skill in profile.skills}
        known_skills |= {
            technology.casefold()
            for entry in (*profile.experiences, *profile.projects)
            for technology in entry.technologies
        }

        def check(condition, message):
            if not condition:
                raise DocumentError("Generated CV edits " + message)

        section_ids = [edit.section_id for edit in self.sections]
        check(len(section_ids) == len(set(section_ids)), "repeat a section")
        for edit in self.sections:
            section = sections.get(edit.section_id)
            check(section is not None and section.entries, "reference an unknown section")
            entries = {entry.id: entry for entry in section.entries}
            entry_ids = [entry.entry_id for entry in edit.entries]
            check(
                len(entry_ids) == len(set(entry_ids)) and set(entry_ids) <= set(entries),
                "reference duplicate or unknown entries",
            )
            for entry_edit in edit.entries:
                entry = entries[entry_edit.entry_id]
                check(
                    bool(entry_edit.bullets) == bool(entry.bullets),
                    "add bullets to an entry without any, or remove all of an entry's bullets",
                )
                bullet_ids = [
                    bullet.source_id
                    for bullet in entry_edit.bullets
                    if bullet.source_id != NEW_BULLET
                ]
                check(
                    len(bullet_ids) == len(set(bullet_ids))
                    and set(bullet_ids) <= {bullet.id for bullet in entry.bullets},
                    "reference duplicate bullets or bullets of another entry",
                )

        group_ids = [edit.group_id for edit in self.skill_groups]
        check(
            len(group_ids) == len(set(group_ids)) and set(group_ids) <= set(groups),
            "reference duplicate or unknown skill groups",
        )
        for edit in self.skill_groups:
            names = [skill.casefold() for skill in edit.skills]
            check(len(names) == len(set(names)), "repeat a skill")
            check(set(names) <= known_skills, "add skills that are not in the CV or profile")

        return self
