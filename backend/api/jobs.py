"""Filter options and the classified jobs catalogue."""

from typing import get_args

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api.catalogue import JobFilters
from backend.api.deps import AppContext, CurrentUser
from backend.utils.job_taxonomy import ContractType, JobCategory, Seniority

router = APIRouter(prefix="/api", tags=["jobs"])


class Options(BaseModel):
    categories: list[str]
    contract_types: list[str]
    seniorities: list[str]
    user_id: str
    demo: bool


class JobSummary(BaseModel):
    job_id: str
    title: str | None
    company: str | None
    location: str | None
    description: str | None
    source: str | None
    posting_url: str | None
    application_url: str | None
    published_at: str | None
    published_text: str | None
    seniority: str | None
    contract_type: str | None
    category: str
    is_tech: bool | None


class JobPage(BaseModel):
    jobs: list[JobSummary]
    next_cursor: str | None


@router.get("/options")
def options(context: AppContext, user: CurrentUser) -> Options:
    return Options(
        categories=list(get_args(JobCategory)),
        contract_types=list(get_args(ContractType)),
        seniorities=list(get_args(Seniority)),
        user_id=user,
        demo=context.settings.demo,
    )


@router.get("/jobs")
def list_jobs(
    context: AppContext,
    tech: str = "tech",
    seniority: str = "entry,junior",
    category: str = "",
    contract: str = "",
    q: str = "",
    cursor: str | None = None,
) -> JobPage:
    filters = JobFilters(
        tech=tech,
        seniorities=tuple(filter(None, seniority.split(","))),
        category=category,
        contract_type=contract,
        search=q.strip(),
    )
    return context.catalogue.page(filters, cursor=cursor)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, context: AppContext) -> JobSummary:
    job = context.catalogue.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job
