"""One SQS generation per worker; ECS scales the number of workers."""

import os
import threading

import boto3
from botocore.config import Config

from backend.documents.bedrock import BedrockGenerator
from backend.documents.service import DocumentService
from backend.documents.storage import GenerationStore
from backend.utils.runtime import poll, shutdown_event


def main():
    session = boto3.Session()
    timeout = int(os.environ.get("DOCUMENT_TIMEOUT_SECONDS", "1200"))
    store = GenerationStore(
        session.resource("dynamodb").Table(os.environ["GENERATIONS_TABLE"]),
        session.client("s3"),
        os.environ["DOCUMENTS_BUCKET"],
    )
    client = session.client(
        "bedrock-runtime",
        config=Config(
            connect_timeout=10,
            read_timeout=240,
            retries={"mode": "adaptive", "total_max_attempts": 3},
        ),
    )
    service = DocumentService(
        store, BedrockGenerator(client), os.environ["DOCUMENT_MODEL_ID"], timeout=timeout
    )

    def process(message):
        # A stuck SDK/compiler must not keep an SQS message in flight indefinitely.
        deadline = threading.Timer(timeout, lambda: os._exit(1))
        deadline.daemon = True
        deadline.start()
        try:
            return service.process(message)
        finally:
            deadline.cancel()

    poll(session.client("sqs"), os.environ["QUEUE_URL"], process, shutdown_event())


if __name__ == "__main__":
    main()
