"""Mailbox sync, tracked applications and the jobs in LinkedIn alert emails."""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, StringConstraints

from backend.api import generations
from backend.api.catalogue import posting_of
from backend.api.deps import AppContext, CurrentUser
from backend.mail.classifier import ApplicationStatus
from backend.mail.linkedin import JobPageError, job_url
from backend.mail.mailbox import MailboxError
from backend.mail.matching import company_key, find_application, same_role
from backend.mail.storage import MAIL_JOB_PREFIX, apply_status, timestamp
from backend.mail.sync import attach_job, sync_mailbox

LOG = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["mail"])
AUTO_SYNC_SECONDS = 900
# Time for a confirmation email to arrive, and how long a job is worth asking about.
ASK_AFTER = timedelta(days=1)
STOP_ASKING_AFTER = timedelta(days=30)
LinkedinId = Annotated[str, Path(pattern=r"^[0-9]{6,20}$")]


class MailStatus(BaseModel):
    problem: Literal["credentials", "table"] | None
    syncing: bool
    last_synced_at: str | None = None
    last_error: str | None = None


class ApplicationEvent(BaseModel):
    status: ApplicationStatus
    subject: str
    received_at: str


class Application(BaseModel):
    application_id: str
    company: str
    role: str | None = None
    status: ApplicationStatus
    updated_at: str
    events: list[ApplicationEvent]
    generation_id: str | None = None  # The tailored CV for this job, when one matches.
    job_id: str | None = None  # The job post you opened, when one matches.
    job_url: str | None = None


class ApplicationChange(BaseModel):
    status: ApplicationStatus


class JobOpened(BaseModel):
    job_id: str


class Interest(BaseModel):
    """A job post you opened, kept so a later email can be matched to it."""

    job_id: str
    title: str
    company: str | None = None
    url: str | None = None
    opened_at: str


class AlertJob(BaseModel):
    job_id: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    has_description: bool


class JobAlert(BaseModel):
    alert_id: str
    subject: str
    received_at: str
    jobs: list[AlertJob]


class JobDescription(BaseModel):
    # Left out, the description is read from LinkedIn; given, it is what the user pasted.
    description: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=100000)
    ] = None


def mail_store(context):
    if context.mail is None:
        raise HTTPException(503, "The mail table is not deployed yet")
    return context.mail


def run_sync(context, user):
    """Runs one sync; a second request while one is running does nothing."""
    lock = context.mail_sync_lock
    if not lock.acquire(blocking=False):
        return
    store = context.mail
    try:
        with context.settings.open_mailbox() as mailbox:
            client = context.client("bedrock-runtime")
            sync_mailbox(store, user, mailbox, context.settings.classify_mail(client))
        store.save_sync_state(user, last_synced_at=timestamp(), last_error=None)
    except MailboxError as exc:
        store.save_sync_state(user, last_error=str(exc))
    except Exception as exc:  # Runs in the background, so this is the only way to report it.
        LOG.error("Mail sync failed: %s", type(exc).__name__)
        store.save_sync_state(
            user, last_error="The sync stopped early; it will continue from the same email."
        )
    finally:
        lock.release()


def sync_forever(context, user):
    while True:
        try:
            run_sync(context, user)
        except Exception as exc:  # The next round may work; this thread must not die.
            LOG.error("Mail sync failed: %s", type(exc).__name__)
        time.sleep(AUTO_SYNC_SECONDS)


def start_auto_sync(context, user):
    if context.mail is not None and context.settings.open_mailbox is not None:
        threading.Thread(target=sync_forever, args=(context, user), daemon=True).start()


@router.get("/mail/status")
def mail_status(context: AppContext, user: CurrentUser) -> MailStatus:
    syncing = context.mail_sync_lock.locked()
    if context.mail is None:
        return MailStatus(problem="table", syncing=syncing)
    state = context.mail.sync_state(user)
    return MailStatus(
        problem=None if context.settings.open_mailbox else "credentials",
        syncing=syncing,
        last_synced_at=state.get("last_synced_at"),
        last_error=state.get("last_error"),
    )


@router.post("/mail/sync", status_code=202)
def start_sync(context: AppContext, user: CurrentUser) -> MailStatus:
    mail_store(context)
    if context.settings.open_mailbox is None:
        raise HTTPException(409, "Set MAIL_ADDRESS and MAIL_APP_PASSWORD in .env first")
    threading.Thread(target=run_sync, args=(context, user), daemon=True).start()
    return mail_status(context, user)


def matching_generation(application, tailored):
    job_id = application.get("job_id")
    for item in tailored:  # Newest first.
        if job_id and item["job_id"] == job_id:
            return item["generation_id"]
    key = company_key(application["company"])
    for item in tailored:
        same_company = key and company_key(item["company"]) == key
        if same_company and same_role(item["job_title"], application.get("role")):
            return item["generation_id"]
    return None


def application_view(application, tailored):
    return Application.model_validate(
        {**application, "generation_id": matching_generation(application, tailored)}
    )


def tailored_cvs(context, user):
    rows = generations.list_generations(context, user, job_id=None, status="ready")
    return [row for row in rows if row["company"]]


@router.get("/applications")
def list_applications(context: AppContext, user: CurrentUser) -> list[Application]:
    if context.mail is None:
        return []
    tailored = tailored_cvs(context, user)
    rows = sorted(context.mail.applications(user), key=lambda item: item["updated_at"])
    return [application_view(item, tailored) for item in reversed(rows)]


def opened_job(context, user, job_id):
    """The job's details from where the app holds them, so the browser only names it."""
    if job_id.startswith(MAIL_JOB_PREFIX):
        linkedin_id = job_id.removeprefix(MAIL_JOB_PREFIX)
        job = context.mail.job(user, linkedin_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        title = job.get("title") or f"LinkedIn job {linkedin_id}"
        return {
            "job_id": job_id,
            "title": title,
            "company": job.get("company") or None,
            "url": job_url(linkedin_id),
        }
    item = context.jobs.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if item is None:
        raise HTTPException(404, "Job not found")
    posting = posting_of(item)
    url = posting.get("application_url") or posting.get("posting_url")
    return {
        "job_id": job_id,
        "title": posting.get("title") or "Untitled job",
        "company": posting.get("company"),
        # Scraped text ends up in an href, so only allow web links.
        "url": url if str(url).startswith(("https://", "http://")) else None,
    }


@router.post("/applications/interests", status_code=204)
def note_opened_job(body: JobOpened, context: AppContext, user: CurrentUser) -> None:
    """Interest, not an application: an email or the user has to say that they applied."""
    if context.mail is not None:
        context.mail.save_interest(user, opened_job(context, user, body.job_id), timestamp())


@router.get("/applications/interests")
def list_interests(context: AppContext, user: CurrentUser) -> list[Interest]:
    """Opened jobs to ask about: no email has matched them, and one would have arrived by now."""
    if context.mail is None:
        return []
    now = datetime.now(UTC)
    latest, earliest = (now - ASK_AFTER).isoformat(), (now - STOP_ASKING_AFTER).isoformat()
    waiting = [
        item
        for item in context.mail.interests(user)
        if "application_id" not in item and earliest <= item["opened_at"] <= latest
    ]
    rows = sorted(waiting, key=lambda item: item["opened_at"], reverse=True)
    return [Interest.model_validate(item) for item in rows]


def known_interest(context, user, job_id):
    interest = mail_store(context).interest(user, job_id)
    if interest is None:
        raise HTTPException(404, "Job not found")
    return interest


@router.post("/applications/interests/{job_id}/applied")
def mark_applied(job_id: str, context: AppContext, user: CurrentUser) -> Application:
    store = mail_store(context)
    interest = known_interest(context, user, job_id)
    company = interest.get("company") or "Unknown company"
    match = find_application(store.applications(user), company, interest["title"])
    application = match or store.new_application(user, company, interest["title"])
    if not application.get("job_id"):
        application = attach_job(application, interest)
    saved = apply_status(application, "applied", "Marked by you", timestamp())
    store.save_application(saved)
    store.link_interest(user, job_id, saved["application_id"])
    return application_view(saved, tailored_cvs(context, user))


@router.delete("/applications/interests/{job_id}", status_code=204)
def dismiss_interest(job_id: str, context: AppContext, user: CurrentUser) -> None:
    mail_store(context).delete_interest(user, job_id)


@router.patch("/applications/{application_id}")
def change_application(
    application_id: str, body: ApplicationChange, context: AppContext, user: CurrentUser
) -> Application:
    store = mail_store(context)
    application = store.application(user, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")
    changed = apply_status(application, body.status, "Changed by you", timestamp())
    store.save_application(changed)
    return application_view(changed, tailored_cvs(context, user))


@router.delete("/applications/{application_id}", status_code=204)
def delete_application(application_id: str, context: AppContext, user: CurrentUser) -> None:
    """Only the entry goes: its emails stay marked as read, so a sync will not bring it back."""
    mail_store(context).delete_application(user, application_id)


def job_view(job):
    linkedin_id = job["linkedin_id"]
    return AlertJob(
        job_id=f"{MAIL_JOB_PREFIX}{linkedin_id}",
        title=job.get("title") or f"LinkedIn job {linkedin_id}",
        company=job.get("company") or None,
        location=job.get("location") or None,
        url=job_url(linkedin_id),
        has_description=bool(job.get("description")),
    )


@router.get("/mail/alerts")
def list_alerts(context: AppContext, user: CurrentUser) -> list[JobAlert]:
    if context.mail is None:
        return []
    store = context.mail
    saved = store.jobs(user)
    alerts = sorted(store.alerts(user), key=lambda item: item["received_at"], reverse=True)
    return [
        JobAlert(
            **{name: alert[name] for name in ("alert_id", "subject", "received_at")},
            jobs=[job_view(saved.get(job["linkedin_id"], job)) for job in alert["jobs"]],
        )
        for alert in alerts
    ]


@router.post("/mail/jobs/{linkedin_id}/description")
def load_job_description(
    linkedin_id: LinkedinId, body: JobDescription, context: AppContext, user: CurrentUser
) -> AlertJob:
    """Called once for a job the user wants to tailor to; other jobs are never fetched."""
    store = mail_store(context)
    job = store.job(user, linkedin_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if body.description:
        details = {"description": body.description}
    elif job.get("description"):
        return job_view(job)
    else:
        try:
            details = context.settings.fetch_job(linkedin_id)
        except JobPageError as exc:
            raise HTTPException(502, str(exc)) from None
    return job_view(store.save_job_details(user, linkedin_id, details))
