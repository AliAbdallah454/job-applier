"""Long-polling scraper and classifier workers for ECS, with renewable SQS leases."""

import argparse
import json
import logging
import os
import signal
import threading
from contextlib import contextmanager

import boto3
from typesafe_sdk import RetryPolicy, TypeSafeClient

from backend.jobs.collector import BoardConfig
from backend.jobs.job_fetchers import create_fetcher
from backend.jobs.results import ClassificationStore
from backend.jobs.storage import DynamoJobStore
from backend.jobs.typesafe import JevClassifier

LOG = logging.getLogger(__name__)


@contextmanager
def visibility_heartbeat(sqs, queue_url, receipt):
    stop = threading.Event()

    def renew():
        while not stop.wait(60):
            try:
                sqs.change_message_visibility(
                    QueueUrl=queue_url,
                    ReceiptHandle=receipt,
                    VisibilityTimeout=600,
                )
            except Exception as exc:
                LOG.warning("Queue heartbeat failed (%s)", type(exc).__name__)

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def process_scrape(body, store):
    board = BoardConfig.model_validate(body["board"])
    limit = body.get("limit", 100)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Per-board limit must be between 1 and 100")
    with create_fetcher(board.source, board.board, board.company, region=board.region) as fetcher:
        jobs = fetcher.fetch(limit=limit)
    store.save_jobs(
        [
            {**job.model_dump(mode="json"), "board": board.board, "region": board.region}
            for job in jobs
        ]
    )
    return len(jobs)


def process_classify(body, jobs, store, classifier, model):
    job_id = body["job_id"]
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("Invalid job_id")
    item = jobs.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if item is None:
        return "deleted"
    return store.classify(item, classifier, model=model)


def handle_message(sqs, queue_url, message, process):
    """Delete only after successful persistence; failures retry and eventually reach a DLQ."""
    receipt = message["ReceiptHandle"]
    with visibility_heartbeat(sqs, queue_url, receipt):
        result = process(json.loads(message["Body"]))
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
    return result


def poll(sqs, queue_url, process, stop):
    while not stop.is_set():
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=600,
        )
        for message in response.get("Messages", []):
            if stop.is_set():
                sqs.change_message_visibility(
                    QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"], VisibilityTimeout=0
                )
                return
            try:
                result = handle_message(sqs, queue_url, message, process)
                LOG.info("Message completed: %s", result)
            except Exception as exc:
                LOG.error("Message failed (%s); retained for retry/DLQ", type(exc).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["scrape", "classify"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    jobs_store = DynamoJobStore(os.environ["JOBS_TABLE"])
    jobs = jobs_store.table
    sqs = boto3.client("sqs")
    queue_url = os.environ["QUEUE_URL"]
    if args.mode == "scrape":
        poll(sqs, queue_url, lambda body: process_scrape(body, jobs_store), stop)
    else:
        results = DynamoJobStore(os.environ["CLASSIFICATIONS_TABLE"]).table
        model = os.environ.get("JEV_MODEL", "jev-latest")
        with TypeSafeClient(model=model, timeout=60, retry=RetryPolicy(max_retries=2)) as client:
            store = ClassificationStore(jobs, results)
            classifier = JevClassifier(client, model=model)
            poll(
                sqs,
                queue_url,
                lambda body: process_classify(body, jobs, store, classifier, model),
                stop,
            )


if __name__ == "__main__":
    main()
