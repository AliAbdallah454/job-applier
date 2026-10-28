"""Queue company boards or backfill classification work using the shipped registry."""

import argparse
import json
import os
from pathlib import Path

import boto3

from backend.jobs.collector import load_boards
from backend.jobs.job_fetchers import FETCHERS


def enqueue(sqs, queue_url, boards, *, max_jobs_per_board=None):
    if max_jobs_per_board is not None and max_jobs_per_board < 1:
        raise ValueError("max_jobs_per_board must be positive")

    seen = set()
    count = 0

    for board in boards:
        identity = (board.source, board.region, board.board)
        if identity in seen:
            continue

        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(
                {"board": board.model_dump(), "max_jobs_per_board": max_jobs_per_board}
            ),
        )
        seen.add(identity)
        count += 1

    return count


def backfill(table, sqs, queue_url):
    scan_args = {"ProjectionExpression": "job_id"}
    count = 0

    while True:
        page = table.scan(**scan_args)

        for item in page.get("Items", []):
            sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"job_id": item["job_id"]}))
            count += 1
        if not page.get("LastEvaluatedKey"):
            return count

        scan_args["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["scrape", "classify"], default="scrape")
    parser.add_argument("--boards-file", type=Path, default=Path("config/job-board-directory.csv"))
    parser.add_argument(
        "--max-boards",
        type=int,
        help="Maximum company boards; default all",
        default=None
        if os.environ.get("MAX_BOARDS", "all") == "all"
        else int(os.environ["MAX_BOARDS"]),
    )
    parser.add_argument(
        "--max-jobs-per-board",
        type=int,
        help="Maximum jobs per company board; default all",
        default=None
        if os.environ.get("JOBS_PER_BOARD", "all") == "all"
        else int(os.environ["JOBS_PER_BOARD"]),
    )
    parser.add_argument("--platforms", nargs="+", choices=list(FETCHERS))
    args = parser.parse_args()

    if args.max_boards is not None and args.max_boards < 1:
        parser.error("--max-boards must be positive")
    if args.max_jobs_per_board is not None and args.max_jobs_per_board < 1:
        parser.error("--max-jobs-per-board must be positive")
    sqs = boto3.client("sqs")

    if args.mode == "classify":
        table = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
        print(f"Queued {backfill(table, sqs, os.environ['CLASSIFY_QUEUE_URL'])} job IDs")
        return

    boards = load_boards(args.boards_file)
    if args.platforms:
        boards = [board for board in boards if board.source in args.platforms]
    boards = list({(b.source, b.region, b.board): b for b in boards}.values())[: args.max_boards]
    if not boards:
        parser.error("No boards selected")
    count = enqueue(
        sqs, os.environ["QUEUE_URL"], boards, max_jobs_per_board=args.max_jobs_per_board
    )
    print(f"Queued {count} company boards")


if __name__ == "__main__":
    main()
