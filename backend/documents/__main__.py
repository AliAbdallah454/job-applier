"""Submit document requests and inspect results using deployed stack outputs."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from backend.documents.models import DOCUMENT_MODELS, GenerationInput, Job
from backend.documents.storage import GenerationStore, encode
from backend.profiles.models import CandidateProfile


def stack_outputs(session, name):
    response = session.client("cloudformation").describe_stacks(StackName=name)
    return {item["OutputKey"]: item["OutputValue"] for item in response["Stacks"][0]["Outputs"]}


def load_job(args, session):
    if args.job_file:
        data = json.loads(Path(args.job_file).read_text())
        data.setdefault("job_id", "manual-" + hashlib.sha256(encode(data)).hexdigest())
    else:
        outputs = stack_outputs(session, args.jobs_stack)
        data = (
            session.resource("dynamodb")
            .Table(outputs["JobsTable"])
            .get_item(Key={"job_id": args.job_id}, ConsistentRead=True)
            .get("Item")
        )
        if data is None:
            raise ValueError("Job not found")
        data = {
            key: data[key] for key in ("job_id", "title", "description", "company") if key in data
        }
    return Job.model_validate(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["generate", "status", "retry"])
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--application-stack", default="Application")
    parser.add_argument("--jobs-stack", default="JobPipeline")
    parser.add_argument("--region")
    parser.add_argument("--profile")
    parser.add_argument("--generation-id")
    jobs = parser.add_mutually_exclusive_group()
    jobs.add_argument("--job-id")
    jobs.add_argument("--job-file", help="JSON with title, description, optional company/job_id")
    parser.add_argument("--cv-file", help="Your LaTeX CV to tailor")
    parser.add_argument("--model", help="Model ID; defaults to the first configured model")
    parser.add_argument("--force", action="store_true", help="Create a new generation revision")
    args = parser.parse_args()
    if args.command == "generate" and not ((args.job_id or args.job_file) and args.cv_file):
        parser.error("generate requires --cv-file and --job-id or --job-file")
    if args.command != "generate" and not args.generation_id:
        parser.error("status/retry requires --generation-id")

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        outputs = stack_outputs(session, args.application_stack)
        s3 = session.client("s3")
        store = GenerationStore(
            session.resource("dynamodb").Table(outputs["DocumentGenerationsTable"]),
            s3,
            outputs["DocumentsBucketName"],
        )
        if args.command == "generate":
            data = (
                session.resource("dynamodb")
                .Table(outputs["ProfilesTable"])
                .get_item(Key={"user_id": args.user_id}, ConsistentRead=True)
                .get("Item")
            )
            if data is None:
                raise ValueError("Profile not found; seed or create it first")
            models = [model.id for model in DOCUMENT_MODELS]
            if args.model and args.model not in models:
                raise ValueError("Unknown model; choose one of " + ", ".join(models))
            request = GenerationInput(
                profile=CandidateProfile.model_validate(data),
                job=load_job(args, session),
                model_id=args.model or models[0],
                cv_latex=Path(args.cv_file).read_text(encoding="utf-8"),
            )
            record = store.submit(
                request, session.client("sqs"), outputs["DocumentQueueUrl"], force=args.force
            )
        else:
            record = store.get(args.user_id, args.generation_id)
            if record is None:
                raise ValueError("Generation not found for this user")
            if args.command == "retry":
                store.enqueue(
                    session.client("sqs"),
                    outputs["DocumentQueueUrl"],
                    args.user_id,
                    args.generation_id,
                )

        result = {
            key: record[key]
            for key in (
                "user_id",
                "generation_id",
                "job_id",
                "status",
                "review_required",
                "created_at",
            )
        }
        if record.get("last_error"):
            result["last_error"] = record["last_error"]
            result["failure_stage"] = record.get("failure_stage")
            result["error_detail"] = record.get("error_detail")
        downloadable = record.get("artifacts", {}) if record["status"] == "ready" else {}
        downloadable = {**downloadable, **record.get("failure_artifacts", {})}
        if args.command == "status" and downloadable:
            result["downloads_expire_in_seconds"] = 900
            result["downloads"] = {}
            for name, reference in downloadable.items():
                params = {"Bucket": reference["bucket"], "Key": reference["key"]}
                if reference.get("version_id"):
                    params["VersionId"] = reference["version_id"]
                result["downloads"][name] = s3.generate_presigned_url(
                    "get_object", Params=params, ExpiresIn=900
                )
        print(json.dumps(result, indent=2))
        return 0
    except (ClientError, BotoCoreError) as exc:
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        print(
            f"AWS request failed ({code}); check credentials, stack outputs and permissions",
            file=sys.stderr,
        )
        return 1
    except (ValueError, OSError) as exc:
        # Pydantic errors may include sensitive profile input; show only their type.
        detail = type(exc).__name__ if isinstance(exc, ValidationError) else str(exc)
        print(f"Error: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
