"""Enrich jobs in an existing DynamoDB table using TypeSafe AI's Jev model."""

import argparse
import os
import sys

from botocore.exceptions import BotoCoreError, ClientError
from typesafe_sdk import RetryPolicy, TypeSafeClient, TypeSafeError

from backend.jobs.classification.service import classify_table
from backend.jobs.classification.typesafe import JevClassifier
from backend.jobs.storage.jobs import DynamoJobStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default=os.environ.get("DYNAMODB_TABLE", "job-postings"))
    parser.add_argument(
        "--results-table", default=os.environ.get("CLASSIFICATIONS_TABLE", "job-classifications")
    )
    parser.add_argument("--aws-region", help="Otherwise use the standard AWS SDK configuration")
    parser.add_argument("--model", default="jev-latest")
    parser.add_argument(
        "--limit", type=int, default=100, help="Maximum classification attempts (default 100)"
    )
    parser.add_argument("--page-size", type=int, default=100, help="DynamoDB scan page size")
    parser.add_argument("--force", action="store_true", help="Reclassify unchanged jobs too")
    args = parser.parse_args()

    if args.limit < 1 or args.page_size < 1:
        parser.error("--limit and --page-size must be positive")
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        print("Set TYPESAFE_API_KEY securely in your environment before running.", file=sys.stderr)
        return 1

    try:
        table = DynamoJobStore(args.table, region=args.aws_region).table
        results = DynamoJobStore(args.results_table, region=args.aws_region).table

        with TypeSafeClient(
            model=args.model,
            timeout=60,
            retry=RetryPolicy(max_retries=2),
        ) as client:
            counts = classify_table(
                table,
                JevClassifier(client, model=args.model),
                results_table=results,
                provider="typesafe",
                model=args.model,
                limit=args.limit,
                page_size=args.page_size,
                force=args.force,
                progress=lambda message: print(message, file=sys.stderr),
            )
    except KeyboardInterrupt:
        print("Interrupted; previously saved classifications remain in DynamoDB.", file=sys.stderr)
        return 130
    except (BotoCoreError, ClientError, TypeSafeError, ValueError) as exc:
        # Do not print provider response bodies, job descriptions or credential values.
        print(
            f"Classification stopped ({type(exc).__name__}); check credentials, region, "
            "table permissions, network access and provider status. Earlier writes remain saved.",
            file=sys.stderr,
        )
        return 1

    print("; ".join(f"{name}={count}" for name, count in counts.items()))
    return 2 if counts["failed"] or counts["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
