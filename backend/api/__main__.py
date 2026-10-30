"""Run the local API. Binds to localhost and has no login; it is not deployed."""

import argparse

import boto3
import uvicorn
from botocore.exceptions import BotoCoreError, ClientError

from backend.api.app import create_app
from backend.api.deps import Context, resolve_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region")
    parser.add_argument("--profile")
    parser.add_argument("--user-id", default="ali")
    parser.add_argument("--application-stack", default="Application")
    parser.add_argument("--jobs-stack", default="JobPipeline")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--demo", action="store_true", help="Use example data in memory; no AWS or Bedrock"
    )
    args = parser.parse_args()

    if args.demo:
        from backend.api.demo import start_demo  # Needs Moto, a development dependency.

        context = start_demo(args.user_id)
    else:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        try:
            settings = resolve_settings(
                session,
                args.user_id,
                application_stack=args.application_stack,
                jobs_stack=args.jobs_stack,
            )
        except (BotoCoreError, ClientError, KeyError) as exc:
            parser.error(f"Cannot resolve stack outputs: {exc}")
        context = Context(settings, profile=args.profile, region=args.region)

    uvicorn.run(create_app(context), host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
