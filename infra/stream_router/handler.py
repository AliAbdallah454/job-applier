"""DynamoDB stream -> classifier queue. Ignores last_seen-only refreshes."""

import json
import os

import boto3


def needs_classification(record):
    if record.get("eventName") not in {"INSERT", "MODIFY"}:
        return False
    stream = record["dynamodb"]
    new_job = stream.get("NewImage", {}).get("job", {}).get("M", {})
    old_job = stream.get("OldImage", {}).get("job", {}).get("M", {})
    if not new_job:
        return False
    return record["eventName"] == "INSERT" or any(
        new_job.get(key) != old_job.get(key) for key in ("title", "description")
    )


def handler(event, context):
    sqs = boto3.client("sqs")
    failures = []
    for record in event["Records"]:
        try:
            if needs_classification(record):
                job_id = record["dynamodb"]["NewImage"]["job_id"]["S"]
                sqs.send_message(
                    QueueUrl=os.environ["QUEUE_URL"],
                    MessageBody=json.dumps({"job_id": job_id}),
                )
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
