"""Consume company-board messages, fetch postings and upsert them into DynamoDB."""

import argparse
import os

import boto3

from backend.jobs.collector import BoardConfig
from backend.jobs.job_fetchers import create_fetcher
from backend.jobs.storage.jobs import DynamoJobStore
from backend.jobs.workers.runtime import poll, shutdown_event


def process_scrape(body, store):
    board = BoardConfig.model_validate(body["board"])
    # Accept the old key for messages queued before the CLI rename.
    limit = body.get("max_jobs_per_board", body.get("limit"))
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ValueError("max_jobs_per_board must be a positive integer or null")

    with create_fetcher(board.source, board.board, board.company, region=board.region) as fetcher:
        jobs = fetcher.fetch(limit=limit)

    store.save_jobs(
        [
            {**job.model_dump(mode="json"), "board": board.board, "region": board.region}
            for job in jobs
        ]
    )

    return len(jobs)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    stop = shutdown_event()
    store = DynamoJobStore(os.environ["JOBS_TABLE"])
    sqs = boto3.client("sqs")
    poll(sqs, os.environ["QUEUE_URL"], lambda body: process_scrape(body, store), stop)


if __name__ == "__main__":
    main()
