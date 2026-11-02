"""Settings, AWS clients and the current user, shared by every route."""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import boto3
import jwt
from fastapi import Depends, HTTPException, Request

from backend.api.auth0 import Auth0
from backend.api.catalogue import JobCatalogue
from backend.documents.__main__ import stack_outputs
from backend.documents.models import DOCUMENT_MODELS, ModelOption
from backend.documents.render import compile_pdf, source_line
from backend.documents.storage import GenerationStore
from backend.mail.classifier import MailClassifier
from backend.mail.linkedin import fetch_job
from backend.mail.storage import MailStore

SESSION_COOKIE = "session"


@dataclass(frozen=True)
class Settings:
    user_id: str
    jobs_table: str
    classifications_table: str
    profiles_table: str
    generations_table: str
    documents_bucket: str
    document_queue_url: str
    document_models: tuple[ModelOption, ...] = DOCUMENT_MODELS
    compile_document: Callable = compile_pdf
    find_source_line: Callable = source_line
    mail_table: str | None = None  # None until the stack with the table is deployed.
    open_mailbox: Callable | None = None  # None until the mailbox settings are in .env.
    classify_mail: Callable = MailClassifier
    fetch_job: Callable = fetch_job
    demo: bool = False
    auth: Auth0 | None = (
        None  # None until the AUTH0_* values are in .env: no login, localhost only.
    )


def resolve_settings(
    session,
    user_id,
    *,
    application_stack="Application",
    jobs_stack="JobPipeline",
    open_mailbox=None,
    auth=None,
):
    application = stack_outputs(session, application_stack)
    jobs = stack_outputs(session, jobs_stack)
    return Settings(
        user_id=user_id,
        jobs_table=jobs["JobsTable"],
        classifications_table=jobs["ClassificationsTable"],
        profiles_table=application["ProfilesTable"],
        generations_table=application["DocumentGenerationsTable"],
        documents_bucket=application["DocumentsBucketName"],
        document_queue_url=application["DocumentQueueUrl"],
        mail_table=application.get("MailTable"),
        open_mailbox=open_mailbox,
        auth=auth,
    )


class Context:
    """Boto3 resources are not thread-safe, so each request thread owns its own."""

    def __init__(self, settings, *, profile=None, region=None):
        self.settings = settings
        self.profile = profile
        self.region = region
        self.local = threading.local()
        self.mail_sync_lock = threading.Lock()

    def session(self):
        if not hasattr(self.local, "session"):
            self.local.session = boto3.Session(profile_name=self.profile, region_name=self.region)
        return self.local.session

    def client(self, name):
        if not hasattr(self.local, name):
            setattr(self.local, name, self.session().client(name))
        return getattr(self.local, name)

    @property
    def dynamodb(self):
        if not hasattr(self.local, "dynamodb"):
            self.local.dynamodb = self.session().resource("dynamodb")
        return self.local.dynamodb

    @property
    def s3(self):
        return self.client("s3")

    @property
    def profiles(self):
        return self.dynamodb.Table(self.settings.profiles_table)

    @property
    def jobs(self):
        return self.dynamodb.Table(self.settings.jobs_table)

    @property
    def catalogue(self):
        settings = self.settings
        return JobCatalogue(self.dynamodb, settings.jobs_table, settings.classifications_table)

    @property
    def generations(self):
        return GenerationStore(
            self.dynamodb.Table(self.settings.generations_table),
            self.s3,
            self.settings.documents_bucket,
        )

    @property
    def mail(self):
        if self.settings.mail_table is None:
            return None
        return MailStore(self.dynamodb.Table(self.settings.mail_table))


def get_context(request: Request) -> Context:
    return request.app.state.context


AppContext = Annotated[Context, Depends(get_context)]


def current_user(request: Request, context: AppContext) -> str:
    """The user of this request, from the session cookie when login is set up.

    The one account allowed holds the data of the configured user, as the mailbox and the existing
    data are that user's. Each account gets its own data once mail is per user.
    """
    settings = context.settings
    if settings.auth is None:
        return settings.user_id
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Log in first")
    try:
        claims = settings.auth.verify(token)
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(503, "Auth0 could not be reached. Try again.") from None
    except jwt.PyJWTError:
        raise HTTPException(401, "Log in again") from None
    if claims["sub"] != settings.auth.owner_id:
        raise HTTPException(403, "This account is not allowed")
    return settings.user_id


CurrentUser = Annotated[str, Depends(current_user)]
