"""Settings, AWS clients and the current user, shared by every route."""

import json
import threading
from dataclasses import dataclass
from typing import Annotated

import boto3
from fastapi import Depends, Request

from backend.api.catalogue import JobCatalogue
from backend.documents.__main__ import stack_outputs
from backend.documents.storage import GenerationStore


@dataclass(frozen=True)
class Settings:
    user_id: str
    jobs_table: str
    classifications_table: str
    profiles_table: str
    generations_table: str
    documents_bucket: str
    document_queue_url: str
    document_models: list[dict]
    demo: bool = False


def resolve_settings(
    session, user_id, *, application_stack="Application", jobs_stack="JobPipeline"
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
        document_models=json.loads(application["DocumentModels"]),
    )


class Context:
    """Boto3 resources are not thread-safe, so each request thread owns its own."""

    def __init__(self, settings, *, profile=None, region=None):
        self.settings = settings
        self.profile = profile
        self.region = region
        self.local = threading.local()

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


def get_context(request: Request) -> Context:
    return request.app.state.context


AppContext = Annotated[Context, Depends(get_context)]


def current_user(context: AppContext) -> str:
    """The configured local user; replace this one function when Cognito is added."""
    return context.settings.user_id


CurrentUser = Annotated[str, Depends(current_user)]
