"""Run the API. Binds to localhost; login needs the AUTH0_* values in .env or the environment.

With `--host` set to anything else, such as 0.0.0.0 in a container, it is deployed: it refuses to
start without login, accepts the host name of AUTH0_APP_URL and closes its API docs.
"""

import argparse
import os
from urllib.parse import urlsplit

import boto3
import uvicorn
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values

from backend.api.app import create_app
from backend.api.auth0 import Auth0
from backend.api.deps import Context, resolve_settings
from backend.api.mail import start_auto_sync
from backend.mail.mailbox import configured_mailbox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region")
    parser.add_argument("--profile")
    parser.add_argument("--user-id", default="ali")
    parser.add_argument("--application-stack", default="Application")
    parser.add_argument("--jobs-stack", default="JobPipeline")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--demo", action="store_true", help="Use example data in memory; no AWS or Bedrock"
    )
    args = parser.parse_args()

    deployed = args.host not in {"127.0.0.1", "localhost"}
    public_host = None
    if args.demo:
        if deployed:
            parser.error("--demo has no login, so it only runs on localhost")
        from backend.api.demo import start_demo  # Needs Moto, a development dependency.

        context = start_demo(args.user_id)
    else:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        values = {**dotenv_values(".env"), **os.environ}
        try:
            auth = Auth0.from_values(values)
        except ValueError as exc:
            parser.error(str(exc))
        if deployed:
            if auth is None or not auth.owner_id:
                parser.error(f"--host {args.host} needs login: set the AUTH0_* values")
            public_host = urlsplit(auth.app_url).hostname
            if public_host in {"localhost", "127.0.0.1"}:
                parser.error(f"--host {args.host} needs AUTH0_APP_URL set to the public address")
            if urlsplit(auth.callback_url).hostname != public_host:
                parser.error("AUTH0_CALLBACK_URL must be on the same host as AUTH0_APP_URL")
        try:
            settings = resolve_settings(
                session,
                args.user_id,
                application_stack=args.application_stack,
                jobs_stack=args.jobs_stack,
                # Only these settings are taken from .env, so it cannot change anything else.
                open_mailbox=configured_mailbox(values),
                auth=auth,
            )
        except (BotoCoreError, ClientError, KeyError) as exc:
            parser.error(f"Cannot resolve stack outputs: {exc}")
        context = Context(settings, profile=args.profile, region=args.region)

    start_auto_sync(context, args.user_id)
    app = create_app(context, public_host=public_host)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
