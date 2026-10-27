"""Fetch employer job boards and save postings to DynamoDB (or an explicit JSON export)."""

import argparse
import json
import os
import sys
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from backend.jobs.collector import collect_jobs, load_boards, save_report
from backend.jobs.job_fetchers import FETCHERS, FetchError, create_fetcher
from backend.jobs.storage import DynamoJobStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "query", nargs="?", default="", help="Optional words to match in title/description"
    )
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--source", choices=list(FETCHERS), help="Single-board mode")
    sources.add_argument(
        "--boards-file", type=Path, help="JSON or CSV registry; defaults to config/boards.json"
    )
    parser.add_argument("--board", help="Employer token; requires --source")
    parser.add_argument("--company", help="Employer display name; defaults to board token")
    parser.add_argument("--region", choices=["global", "eu"], default="global", help="Lever only")
    parser.add_argument("--location", help="Case-insensitive location substring")
    parser.add_argument(
        "--limit", type=int, help="Maximum matching jobs per board (1–100); default all"
    )
    parser.add_argument(
        "--total-limit", type=int, help="Batch mode: maximum jobs across all companies combined"
    )
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--output", type=Path, help="Export JSON instead of using DynamoDB")
    destination.add_argument(
        "--table", help="DynamoDB table; default DYNAMODB_TABLE or job-postings"
    )
    parser.add_argument("--aws-region", help="AWS region; otherwise use the AWS SDK configuration")
    parser.add_argument(
        "--platforms",
        nargs="+",
        choices=list(FETCHERS),
        help="Batch mode: select platforms from the registry",
    )
    parser.add_argument(
        "--max-boards", type=int, help="Batch mode: test only the first N supported boards"
    )
    args = parser.parse_args()
    if bool(args.source) != bool(args.board):
        parser.error("--source and --board must be used together")
    if args.limit is not None and not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100, or omitted for all jobs")
    if args.total_limit is not None and (args.total_limit < 1 or args.source):
        parser.error("--total-limit must be positive and is available only in batch mode")
    if args.max_boards is not None and (args.max_boards < 1 or args.source):
        parser.error("--max-boards must be positive and is available only in batch mode")
    if args.platforms and args.source:
        parser.error("--platforms is available only in batch mode")
    if not args.source and (args.company or args.region != "global"):
        parser.error("For batch collection, set company and region in the boards file")
    if args.source and args.source != "lever" and args.region != "global":
        parser.error("--region eu is supported only by Lever")
    store = None
    if args.output is None:
        try:
            store = DynamoJobStore(
                args.table or os.environ.get("DYNAMODB_TABLE", "job-postings"),
                region=args.aws_region,
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            print(f"Error initializing DynamoDB: {exc}", file=sys.stderr)
            return 1
    destination_name = str(args.output) if args.output else f"DynamoDB table {store.table.name}"

    if not args.source:
        output = args.output
        try:
            boards = load_boards(
                args.boards_file or Path("config/boards.json"),
                progress=lambda message: print(message, flush=True),
            )
            loaded_board_count = len(boards)
            if args.platforms:
                boards = [board for board in boards if board.source in args.platforms]
            if args.max_boards is not None:
                boards = boards[: args.max_boards]
            input_info = {
                "boards_file": str(args.boards_file or Path("config/boards.json")),
                "loaded_supported_rows": loaded_board_count,
                "selected_rows": len(boards),
                "max_boards": args.max_boards,
                "platforms": args.platforms,
            }
            report = collect_jobs(
                boards,
                query=args.query,
                location=args.location,
                limit=args.limit,
                total_limit=args.total_limit,
                progress=lambda message: print(message, flush=True),
                on_update=(
                    store.save_report
                    if store
                    else lambda snapshot: save_report({**snapshot, "input": input_info}, output)
                ),
            )
            report["input"] = input_info
            if store:
                store.save_report(report)
            else:
                save_report(report, output)
        except KeyboardInterrupt:
            print(f"Interrupted. Saved postings remain in {destination_name}", file=sys.stderr)
            return 130
        except (ValueError, OSError, BotoCoreError, ClientError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        summary = report["summary"]
        print(
            f"Saved {summary['total_jobs']} jobs to {destination_name}; "
            f"{summary['successful_boards']} boards succeeded, {summary['failed_boards']} failed."
        )
        if not summary["successful_boards"]:
            print(
                "No board succeeded. No jobs were saved from this run.",
                file=sys.stderr,
            )
            return 1
        return 2 if summary["failed_boards"] else 0

    output = args.output

    print(
        f"Fetching {'all' if args.limit is None else 'up to ' + str(args.limit)} jobs "
        f"from {args.source} board {args.board!r} "
        f"(query={args.query!r}, location={args.location!r})...",
        flush=True,
    )
    try:
        fetcher = create_fetcher(args.source, args.board, args.company, region=args.region)
        with fetcher:
            jobs = fetcher.fetch(args.query, location=args.location, limit=args.limit)
        payload = [job.model_dump(mode="json") for job in jobs]
        if store:
            store.save_jobs(
                [{**job, "board": args.board, "region": args.region} for job in payload]
            )
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    except (FetchError, ValueError, OSError, BotoCoreError, ClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Saved {len(jobs)} job postings to {destination_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
