"""FastAPI application: local-only guards, shared error handling and the feature routers."""

import logging
from urllib.parse import urlsplit

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.api import cvs, generations, jobs, mail, profile
from backend.documents.errors import DocumentError

LOG = logging.getLogger(__name__)
LOCAL_HOSTS = ["localhost", "127.0.0.1"]


def create_app(context=None):
    app = FastAPI(title="job-applier", separate_input_output_schemas=False)
    app.state.context = context
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # There is no login yet, so a page on another site must not be able to spend Bedrock.
        origin = request.headers.get("origin")
        unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
        if unsafe and origin and urlsplit(origin).hostname not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid_value(_, exc):
        # Pydantic errors may include sensitive profile input; show only a summary.
        if isinstance(exc, ValidationError):
            LOG.warning("Stored data failed validation: %s errors", exc.error_count())
            return JSONResponse({"detail": "Stored data is not valid"}, status_code=422)
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(DocumentError)
    async def invalid_document(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    async def aws_unavailable(_, exc):
        LOG.error("AWS request failed: %s", type(exc).__name__)
        return JSONResponse(
            {
                "detail": "AWS could not be reached. Check your AWS login, region and "
                "permissions in the terminal."
            },
            status_code=503,
        )

    for error in (BotoCoreError, ClientError, RuntimeError):
        app.add_exception_handler(error, aws_unavailable)

    for feature in (jobs, profile, cvs, generations, mail):
        app.include_router(feature.router)
    return app
