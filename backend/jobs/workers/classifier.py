"""Consume job IDs, classify current postings with Jev and persist the results."""

import argparse
import os

import boto3
from typesafe_sdk import RetryPolicy, TypeSafeClient

from backend.jobs.classification.typesafe import JevClassifier
from backend.jobs.storage.classifications import ClassificationStore
from backend.jobs.storage.jobs import DynamoJobStore
from backend.utils.runtime import poll, shutdown_event


def process_classify(body, jobs, store, classifier, model):
    job_id = body["job_id"]
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("Invalid job_id")

    item = jobs.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if item is None:
        return "deleted"

    return store.classify(item, classifier, model=model)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    stop = shutdown_event()
    jobs = DynamoJobStore(os.environ["JOBS_TABLE"]).table
    results = DynamoJobStore(os.environ["CLASSIFICATIONS_TABLE"]).table
    model = os.environ.get("JEV_MODEL", "jev-latest")
    sqs = boto3.client("sqs")

    with TypeSafeClient(model=model, timeout=60, retry=RetryPolicy(max_retries=2)) as client:
        store = ClassificationStore(jobs, results)
        classifier = JevClassifier(client, model=model)
        poll(
            sqs,
            os.environ["QUEUE_URL"],
            lambda body: process_classify(body, jobs, store, classifier, model),
            stop,
        )


if __name__ == "__main__":
    main()
