"""Delete every generation record and generated file; uploaded CVs and profiles stay."""

import argparse
import sys
from itertools import batched

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from backend.documents.__main__ import stack_outputs


def generation_keys(table):
    keys = []
    for page in table.meta.client.get_paginator("scan").paginate(
        TableName=table.name, ProjectionExpression="user_id, generation_id", ConsistentRead=True
    ):
        keys.extend(page.get("Items", []))
    return keys


def is_generated(key):
    """users/<id>/generations/... holds generated files, users/<id>/cvs/... the uploaded CVs."""
    parts = key.split("/")
    return len(parts) > 3 and parts[0] == "users" and parts[2] == "generations"


def generated_versions(s3, bucket):
    """The bucket is versioned, so a file only goes once all its versions and markers do."""
    versions = []
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix="users/"):
        versions.extend(
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for item in page.get("Versions", []) + page.get("DeleteMarkers", [])
            if is_generated(item["Key"])
        )
    return versions


def delete_versions(s3, bucket, versions):
    for chunk in batched(versions, 1000):
        response = s3.delete_objects(Bucket=bucket, Delete={"Objects": list(chunk), "Quiet": True})
        if response.get("Errors"):  # These arrive in a successful response.
            first = response["Errors"][0]
            raise ValueError(
                f"{len(response['Errors'])} files were not deleted; first: "
                f"{first['Key']}: {first['Message']}"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="Application")
    parser.add_argument("--region")
    parser.add_argument("--profile", help="AWS profile; otherwise use the current AWS environment")
    parser.add_argument("--yes", action="store_true", help="Delete; otherwise only report")
    args = parser.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        outputs = stack_outputs(session, args.stack)
        table = session.resource("dynamodb").Table(outputs["DocumentGenerationsTable"])
        bucket = outputs["DocumentsBucketName"]
        s3 = session.client("s3")

        keys = generation_keys(table)
        versions = generated_versions(s3, bucket)
        print(f"{len(keys)} records in {table.name}; {len(versions)} file versions in {bucket}.")
        if not args.yes:
            print("Nothing was deleted. Pass --yes to delete them; this cannot be undone.")
            return 0

        # Records first: a run that stops halfway leaves unused files, not links to missing ones.
        with table.batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key=key)
        delete_versions(s3, bucket, versions)
        print("Deleted.")
        return 0

    except (ValueError, KeyError, OSError, BotoCoreError, ClientError) as exc:
        print(f"Error clearing generations: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
