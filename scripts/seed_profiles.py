"""Replace development seed profiles in the Application stack's Profiles table."""

import argparse
import json
import sys
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import BotoCoreError, ClientError

from backend.profiles.models import CandidateProfile

DEMO_PREFIX = "demo-user-"
NAMED_SEED_USERS = {"ali"}
DEFAULT_FIXTURE = Path(__file__).parent / "fixtures" / "demo_profiles.json"


def is_seed_user(user_id):
    return user_id.startswith(DEMO_PREFIX) or user_id in NAMED_SEED_USERS


def load_demo_profiles(path):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Fixture must be a non-empty JSON array of profiles")

    profiles = [CandidateProfile.model_validate(row) for row in rows]
    user_ids = [profile.user_id for profile in profiles]

    if not all(is_seed_user(user_id) for user_id in user_ids):
        raise ValueError(
            f"Seed IDs must start with {DEMO_PREFIX!r} or be an explicit named seed user"
        )
    if len(user_ids) != len(set(user_ids)):
        raise ValueError("Fixture contains duplicate user IDs")

    return profiles


def resolve_table_name(session, stack_name):
    stack = session.client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    if "ProfilesTable" not in outputs:
        raise ValueError(f"Stack {stack_name!r} has no ProfilesTable output")

    return outputs["ProfilesTable"]


def reset_demo_profiles(table, profiles):
    """Reset demo users and named seed users present in this fixture, preserving others."""
    items = [
        CandidateProfile.model_validate(profile).model_dump(mode="json") for profile in profiles
    ]
    if not items or any(not is_seed_user(item["user_id"]) for item in items):
        raise ValueError("Provide profiles using reserved demo IDs or explicit named seed IDs")
    if len({item["user_id"] for item in items}) != len(items):
        raise ValueError("Duplicate demo user IDs")

    if (
        table.key_schema != [{"AttributeName": "user_id", "KeyType": "HASH"}]
        or {"AttributeName": "user_id", "AttributeType": "S"} not in table.attribute_definitions
    ):
        raise ValueError("Profiles table must have a string user_id partition key and no sort key")

    # Collect all keys before changing the table so deletion cannot disturb pagination.
    old_keys = []
    seed_filter = Attr("user_id").begins_with(DEMO_PREFIX)
    for item in items:
        if item["user_id"] in NAMED_SEED_USERS:
            seed_filter = seed_filter | Attr("user_id").eq(item["user_id"])
    paginator = table.meta.client.get_paginator("scan")
    for page in paginator.paginate(
        TableName=table.name,
        ProjectionExpression="user_id",
        FilterExpression=seed_filter,
        ConsistentRead=True,
        PaginationConfig={"PageSize": 100},
    ):
        old_keys.extend({"user_id": item["user_id"]} for item in page.get("Items", []))

    with table.batch_writer() as batch:
        for key in old_keys:
            batch.delete_item(Key=key)

    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)

    return {"deleted": len(old_keys), "inserted": len(items)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="Application")
    parser.add_argument("--region")
    parser.add_argument("--profile", help="AWS profile; otherwise use the current AWS environment")
    parser.add_argument("--file", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()

    try:
        profiles = load_demo_profiles(args.file)
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        table_name = resolve_table_name(session, args.stack)
        table = session.resource("dynamodb").Table(table_name)

        print(f"Resetting {len(profiles)} development seed profiles in {table_name}...", flush=True)
        result = reset_demo_profiles(table, profiles)
        print(
            f"Deleted {result['deleted']} seed profiles; "
            f"inserted {result['inserted']} fresh profiles."
        )
        return 0

    except (ValueError, OSError, BotoCoreError, ClientError) as exc:
        print(f"Error seeding demo profiles: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
