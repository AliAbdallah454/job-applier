"""Shared SQS polling, visibility renewal and graceful worker shutdown."""

import json
import logging
import signal
import threading
from contextlib import contextmanager

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


def shutdown_event():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    return stop
