"""Forget what was read from the mailbox, so tracking starts again from the next sync.

Applications, job emails and the sync position go. Job posts you opened stay, and so do jobs
with a saved description, which tailored CVs still refer to. The mailbox itself is not touched.
"""

import argparse
import sys
from collections import Counter

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from backend.documents.__main__ import stack_outputs

ALWAYS_REMOVED = {"alert", "application", "message", "sync"}


def mail_items(table):
    items = []
    for page in table.meta.client.get_paginator("scan").paginate(
        TableName=table.name,
        ProjectionExpression="user_id, item_id, application_id, #description",
        ExpressionAttributeNames={"#description": "description"},
        ConsistentRead=True,
    ):
        items.extend(page.get("Items", []))
    return items


def kind_of(item):
    return item["item_id"].split("#")[0]


def is_removed(item):
    kind = kind_of(item)
    return kind in ALWAYS_REMOVED or (kind == "job" and not item.get("description"))


def linked_interests(items):
    """Opened jobs whose application is going away, so they can be matched again."""
    return [item for item in items if kind_of(item) == "interest" and "application_id" in item]


def clear(table, items):
    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"user_id": item["user_id"], "item_id": item["item_id"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="Application")
    parser.add_argument("--region")
    parser.add_argument("--profile", help="AWS profile; otherwise use the current AWS environment")
    parser.add_argument("--yes", action="store_true", help="Delete; otherwise only report")
    args = parser.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        table = session.resource("dynamodb").Table(stack_outputs(session, args.stack)["MailTable"])
        items = mail_items(table)
        removed = [item for item in items if is_removed(item)]
        linked = linked_interests(items)
        counts = Counter(kind_of(item) for item in removed)
        print(f"{len(removed)} items to remove from {table.name}: {dict(sorted(counts.items()))}.")
        print(f"{len(items) - len(removed)} items stay; {len(linked)} opened jobs get unlinked.")
        if not args.yes:
            print("Nothing was deleted. Stop the API, then pass --yes; this cannot be undone.")
            return 0

        clear(table, removed)
        for item in linked:
            table.update_item(
                Key={"user_id": item["user_id"], "item_id": item["item_id"]},
                UpdateExpression="REMOVE application_id",
            )
        print("Deleted. Tracking starts again at the next sync.")
        return 0

    except (ValueError, KeyError, OSError, BotoCoreError, ClientError) as exc:
        print(f"Error clearing mail data: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
