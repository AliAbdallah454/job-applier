"""Queue company boards or backfill classification work using the shipped registry."""

import argparse
import json
import os
from pathlib import Path

import boto3

from backend.jobs.collector import load_boards
from backend.jobs.job_fetchers import FETCHERS


def enqueue(sqs, queue_url, boards, *, limit=100):
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    seen = set()
    count = 0
    for board in boards:
        identity = (board.source, board.region, board.board)
        if identity in seen:
            continue
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({"board": board.model_dump(), "limit": limit}),
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
    parser.add_argument("--max-boards", type=int, default=int(os.environ.get("MAX_BOARDS", "100")))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("JOBS_PER_BOARD", "20")))
    parser.add_argument("--platforms", nargs="+", choices=list(FETCHERS))
    args = parser.parse_args()
    if args.max_boards < 1 or not 1 <= args.limit <= 100:
        parser.error("max-boards must be positive and limit must be 1–100")
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
    count = enqueue(sqs, os.environ["QUEUE_URL"], boards, limit=args.limit)
    print(f"Queued {count} company boards")


if __name__ == "__main__":
    main()
