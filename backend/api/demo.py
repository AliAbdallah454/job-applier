"""Run the real API and document service against in-memory AWS, with a fake model and compiler."""

import json
import threading
import time
import uuid
from pathlib import Path

import boto3
from moto import mock_aws

from backend.api.deps import Context, Settings
from backend.documents.errors import DocumentCompilationError
from backend.documents.latex import cv_body
from backend.documents.models import CvChange, TailoredCv
from backend.documents.service import DocumentService
from backend.documents.storage import GenerationStore
from backend.jobs.classification.models import input_hash
from backend.profiles.models import CandidateProfile

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
JOBS = [
    (
        "Junior Software Engineer",
        "Example Labs",
        "Paris, France",
        "junior",
        "software_engineer",
        "permanent",
        "Build Python services and help improve our product.\n\n"
        "Work on APIs, automated tests and reliable releases. "
        "We welcome early-career applicants who enjoy learning and collaborating.",
    ),
    (
        "Machine Learning Intern",
        "Example Research",
        "Lyon, France",
        "entry",
        "ml_engineer",
        "internship",
        "Join a research engineering team for a six-month internship.\n\n"
        "Explore model evaluation, prepare datasets and build reproducible "
        "Python experiments alongside a mentor.",
    ),
    (
        "DevOps Apprentice",
        "Example Cloud",
        "Remote · France",
        "entry",
        "devops_engineer",
        "apprenticeship",
        "An apprenticeship focused on cloud infrastructure and developer tools.\n\n"
        "Learn infrastructure as code, observability and CI/CD through practical "
        "work with an experienced platform team.",
    ),
    (
        "Data Engineer",
        "Example Metrics",
        "Berlin, Germany",
        "junior",
        "data_engineer",
        "permanent",
        "Maintain the pipelines behind our reporting.\n\nPython, SQL and scheduled batch jobs.",
    ),
    (
        "Backend Engineer (compile failure example)",
        "Example Broken",
        "Nantes, France",
        "junior",
        "software_engineer",
        "fixed_term",
        "Pick this job to see how a generation that fails to compile looks.",
    ),
    (
        "Software Engineer",
        "Example Studio",
        "Bordeaux, France",
        "mid",
        "software_engineer",
        "fixed_term",
        "Deliver backend features independently and collaborate on architecture and code reviews.",
    ),
]


def example_pdf(title, lines):
    """A small valid PDF so the preview works without TeX."""
    text = [f"BT /F1 20 Tf 60 770 Td ({title}) Tj /F1 11 Tf 0 -36 Td"]
    text += [f"({line}) Tj 0 -18 Td" for line in lines] + ["ET"]
    stream = "\n".join(text).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body, offsets = b"%PDF-1.4\n", []
    for number, content in enumerate(objects, 1):
        offsets.append(len(body))
        body += b"%d 0 obj\n%s\nendobj\n" % (number, content)
    start = len(body)
    body += b"xref\n0 6\n0000000000 65535 f \n"
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return body + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % start


def fake_generate(request, previous=None):
    time.sleep(3)
    body = (
        cv_body(request.cv_latex)
        .replace(
            "Added bounded retries for intermittent request failures",
            "Added bounded retries so APIs survive request failures",
        )
        .replace(
            "PostgreSQL and wrote pytest tests",
            "PostgreSQL and covered them with automated pytest tests",
        )
    )
    return (
        TailoredCv(
            job_keywords=["Python", "APIs", "testing", "CI/CD", "SQL", "AWS"],
            # The extra line keeps the example changed for a CV without the bullets above.
            cv_body=body + "\nExample line added by demo mode.\n",
            changes=[
                CvChange(
                    change="Reworded the retries bullet around APIs",
                    reason="The posting asks for reliable API work",
                ),
                CvChange(
                    change="Named automated tests in the PostgreSQL bullet",
                    reason="Automated tests are a stated requirement",
                ),
            ],
            letter_paragraphs=[
                f"I am applying for the {request.job.title} role.",
                "This example letter was written by the demo, not by a model.",
            ],
        ),
        {"inputTokens": 0, "outputTokens": 0},
    )


def fake_compile(path):
    source = path.read_text()
    if "Example Broken" in source:
        raise DocumentCompilationError(
            timed_out=False,
            diagnostics=b"! Undefined control sequence.\nl.42 \\resumeItm\n"
            b"                {Example compile failure}\n",
        )
    return example_pdf(path.stem, ["Example document produced by demo mode."])


def run_worker(context):
    settings = context.settings
    session = boto3.Session(region_name=REGION)
    sqs = session.client("sqs")
    store = GenerationStore(
        session.resource("dynamodb").Table(settings.generations_table),
        session.client("s3"),
        settings.documents_bucket,
    )
    service = DocumentService(
        store, fake_generate, settings.document_model_id, compile_document=fake_compile
    )
    while True:
        response = sqs.receive_message(
            QueueUrl=settings.document_queue_url, WaitTimeSeconds=1, VisibilityTimeout=0
        )
        for message in response.get("Messages", []):
            sqs.delete_message(
                QueueUrl=settings.document_queue_url, ReceiptHandle=message["ReceiptHandle"]
            )
            time.sleep(2)  # Long enough to see the queued state.
            try:
                service.process(json.loads(message["Body"]))
            except Exception:
                pass  # Recorded on the generation as failed.


def create_tables(db):
    def table(name, keys, **extra):
        return db.create_table(
            TableName=name,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": key, "KeyType": kind}
                for key, kind in zip(keys, ("HASH", "RANGE"), strict=False)
            ],
            AttributeDefinitions=[
                {"AttributeName": key, "AttributeType": "S"}
                for key in {*keys, *extra.pop("attributes", ())}
            ],
            **extra,
        )

    def index(name, hash_key, range_key):
        return {
            "IndexName": name,
            "KeySchema": [
                {"AttributeName": hash_key, "KeyType": "HASH"},
                {"AttributeName": range_key, "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }

    table("jobs", ["job_id"])
    table("profiles", ["user_id"])
    table(
        "classifications",
        ["job_id"],
        attributes=("tech_status", "seniority_job"),
        GlobalSecondaryIndexes=[index("ByTechSeniority", "tech_status", "seniority_job")],
    )
    table(
        "generations",
        ["user_id", "generation_id"],
        attributes=("job_created",),
        GlobalSecondaryIndexes=[index("ByUserJob", "user_id", "job_created")],
    )


def seed(session, user_id):
    db = session.resource("dynamodb")
    create_tables(db)
    for number, entry in enumerate(JOBS):
        title, company, location, seniority, category, contract, description = entry
        job_id = f"demo-{number}"
        state = {"title": title, "description": description}
        db.Table("jobs").put_item(
            Item={
                "job_id": job_id,
                **state,
                "company": company,
                "location": location,
                "source": "greenhouse",
                "posting_url": "https://example.com",
            }
        )
        db.Table("classifications").put_item(
            Item={
                "job_id": job_id,
                "seniority": seniority,
                "contract_type": contract,
                "is_tech": True,
                "category": category,
                "tech_status": "tech",
                "seniority_job": f"{seniority}#{job_id}",
                "input_hash": input_hash(state),
            }
        )

    profile = json.loads((ROOT / "scripts/fixtures/demo_profiles.json").read_text())[0]
    profile = CandidateProfile.model_validate({**profile, "user_id": user_id})
    db.Table("profiles").put_item(Item=profile.model_dump(mode="json"))

    s3 = session.client("s3")
    s3.create_bucket(Bucket="documents")
    s3.put_bucket_versioning(Bucket="documents", VersioningConfiguration={"Status": "Enabled"})
    s3.put_object(
        Bucket="documents",
        Key=f"users/{user_id}/cvs/{uuid.uuid4().hex}/example_cv.tex",
        Body=(ROOT / "backend/documents/examples/jake_cv.tex").read_bytes(),
    )
    return session.client("sqs").create_queue(QueueName="documents")["QueueUrl"]


def start_demo(user_id):
    mock_aws().start()
    session = boto3.Session(region_name=REGION)
    queue_url = seed(session, user_id)
    context = Context(
        Settings(
            user_id=user_id,
            jobs_table="jobs",
            classifications_table="classifications",
            profiles_table="profiles",
            generations_table="generations",
            documents_bucket="documents",
            document_queue_url=queue_url,
            document_model_id="demo-model",
            demo=True,
        ),
        region=REGION,
    )
    threading.Thread(target=run_worker, args=(context,), daemon=True).start()
    return context
