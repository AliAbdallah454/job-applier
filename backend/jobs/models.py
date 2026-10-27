"""Provider-independent job records."""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, HttpUrl


class JobSource(StrEnum):
    """Where a posting was found; entries do not imply implemented integrations."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    ASHBY = "ashby"
    WORKABLE = "workable"
    RECRUITEE = "recruitee"
    RIPPLING = "rippling"
    PERSONIO = "personio"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    FRANCE_TRAVAIL = "france_travail"
    WELCOME_TO_THE_JUNGLE = "welcome_to_the_jungle"


class JobPost(BaseModel):
    """A job posting, independent of how it is fetched or stored."""

    source: JobSource
    source_id: str | None = None
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str
    location: str | None = None
    posting_url: HttpUrl
    application_url: HttpUrl | None = None
    published_at: AwareDatetime | None = None
    published_text: str | None = None  # Provider label, e.g. "3 weeks ago".
    updated_at: AwareDatetime | None = None


class SavedJobPost(BaseModel):
    """A posting with timestamps recording when our application observed it."""

    job: JobPost
    first_seen: AwareDatetime
    last_seen: AwareDatetime
