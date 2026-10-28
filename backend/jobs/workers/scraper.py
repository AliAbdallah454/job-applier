"""Consume company-board messages, fetch postings and upsert them into DynamoDB."""

import argparse
import logging
import os
import threading
from contextlib import contextmanager

import boto3

from backend.jobs.collector import BoardConfig
from backend.jobs.job_fetchers import create_fetcher
from backend.jobs.storage.jobs import DynamoJobStore
from backend.jobs.workers.runtime import poll, shutdown_event

LOG = logging.getLogger(__name__)
DEFAULT_SCRAPE_TIMEOUT_SECONDS = 900


@contextmanager
def scrape_deadline(seconds, board):
    """Exit a stuck worker so it cannot renew visibility or keep scraping after expiry."""
    if seconds <= 0:
        raise ValueError("Scrape timeout must be positive")

    def expire():
        LOG.error(
            "Scrape deadline exceeded after %s seconds for %s/%s; "
            "exiting without acknowledging the message",
            seconds,
            board.source,
            board.board,
        )
        # A thread exception cannot interrupt blocked I/O in the main thread.
        # Terminating also stops its visibility heartbeat; ECS replaces this task.
        os._exit(1)

    watchdog = threading.Timer(seconds, expire)
    watchdog.daemon = True
    watchdog.start()

    try:
        yield
    finally:
        watchdog.cancel()


def process_scrape(body, store, *, timeout_seconds=DEFAULT_SCRAPE_TIMEOUT_SECONDS):
    board = BoardConfig.model_validate(body["board"])
    # Accept the old key for messages queued before the CLI rename.
    limit = body.get("max_jobs_per_board", body.get("limit"))
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ValueError("max_jobs_per_board must be a positive integer or null")

    with scrape_deadline(timeout_seconds, board):
        with create_fetcher(
            board.source, board.board, board.company, region=board.region
        ) as fetcher:
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
    timeout_seconds = int(os.environ.get("SCRAPE_TIMEOUT_SECONDS", DEFAULT_SCRAPE_TIMEOUT_SECONDS))
    if timeout_seconds < 1:
        raise ValueError("SCRAPE_TIMEOUT_SECONDS must be positive")

    stop = shutdown_event()
    store = DynamoJobStore(os.environ["JOBS_TABLE"])
    sqs = boto3.client("sqs")
    poll(
        sqs,
        os.environ["QUEUE_URL"],
        lambda body: process_scrape(body, store, timeout_seconds=timeout_seconds),
        stop,
    )


if __name__ == "__main__":
    main()
