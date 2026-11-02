"""User-supplied career facts; this is a detailed record, not a condensed CV."""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from backend.profiles.skills import Skills, catalogue_entries
from backend.utils.job_taxonomy import ContractType, JobCategory, Seniority

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CareerDate = Annotated[str, StringConstraints(pattern=r"^\d{4}(-(0[1-9]|1[0-2]))?$")]


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Link(ProfileModel):
    label: Text
    url: HttpUrl


class CareerEntry(ProfileModel):
    """Stable identity and optional dates with year or month precision."""

    id: Text = Field(default_factory=lambda: str(uuid4()))
    start_date: CareerDate | None = None
    end_date: CareerDate | None = None
    current: bool = False

    @model_validator(mode="after")
    def valid_dates(self):
        if self.current and self.end_date is not None:
            raise ValueError("A current entry cannot have an end date")

        if self.start_date and self.end_date:
            # A year-only value denotes a range, not an invented January date.
            earliest_start = self.start_date if "-" in self.start_date else self.start_date + "-01"
            latest_end = self.end_date if "-" in self.end_date else self.end_date + "-12"
            if latest_end < earliest_start:
                raise ValueError("End date cannot be before start date")

        return self


class Experience(CareerEntry):
    organization: Text
    role: Text
    location: Text | None = None
    description: Text
    responsibilities: list[Text] = Field(default_factory=list)
    achievements: list[Text] = Field(default_factory=list)
    technologies: list[Text] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)


class Project(CareerEntry):
    name: Text
    description: Text
    contribution: Text | None = None
    outcomes: list[Text] = Field(default_factory=list)
    technologies: list[Text] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)


class Education(CareerEntry):
    institution: Text
    qualification: Text
    field_of_study: Text | None = None
    description: Text | None = None
    coursework: list[Text] = Field(default_factory=list)
    achievements: list[Text] = Field(default_factory=list)


class Language(ProfileModel):
    name: Text
    proficiency: Text | None = None


class Certification(ProfileModel):
    id: Text = Field(default_factory=lambda: str(uuid4()))
    name: Text
    issuer: Text
    issued_at: CareerDate | None = None
    description: Text | None = None
    credential_url: HttpUrl | None = None


class JobPreferences(ProfileModel):
    roles: list[Text] = Field(default_factory=list)
    categories: list[JobCategory] = Field(default_factory=list)
    seniorities: list[Seniority] = Field(default_factory=list)
    contract_types: list[ContractType] = Field(default_factory=list)
    locations: list[Text] = Field(default_factory=list)
    work_arrangements: list[Literal["onsite", "hybrid", "remote"]] = Field(default_factory=list)
    availability: Text | None = None
    notes: Text | None = None


class CandidateProfile(ProfileModel):
    """One user's descriptive facts, distinct from generated CVs and letters."""

    user_id: Text
    schema_version: Literal[2] = 2
    version: int = Field(default=1, ge=1)
    full_name: Text
    email: Text | None = None
    phone: Text | None = None
    location: Text | None = None
    background: Text | None = None
    links: list[Link] = Field(default_factory=list)
    experiences: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: Skills = Field(default_factory=Skills)
    languages: list[Language] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    additional_information: Text | None = None
    preferences: JobPreferences = Field(default_factory=JobPreferences)

    @model_validator(mode="after")
    def unique_entry_ids(self):
        ids = [
            entry.id
            for entries in (self.experiences, self.projects, self.education, self.certifications)
            for entry in entries
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("Experience, project, education and certification IDs must be unique")

        return self


def upgrade_profile(data):
    """Turn a profile saved with free-text skills into today's shape; others pass through.

    Stored profiles and frozen snapshots may still hold that list, while the API only accepts
    the catalogue. What the catalogue cannot hold moves to additional_information, so no fact the
    user gave is lost.
    """
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        return data
    skills, notes = {}, []
    for old in data["skills"]:
        details = ", ".join(filter(None, (old.get("proficiency"), old.get("description"))))
        entries = catalogue_entries(old["name"])
        for key, skill in entries:
            group = skills.setdefault(key, [])
            if skill not in group:
                group.append(skill)
        if details or not entries:
            notes.append(f"{old['name']}: {details}" if details else old["name"])
    extra = [data.get("additional_information"), notes and f"Skill details: {'; '.join(notes)}"]
    return {
        **data,
        "schema_version": 2,
        "skills": skills,
        "additional_information": "\n\n".join(filter(None, extra)) or None,
    }
