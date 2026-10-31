"""Run the real API and document service against in-memory AWS, with a fake model and compiler."""

import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
from moto import mock_aws

from backend.api.deps import Context, Settings
from backend.documents.errors import DocumentCompilationError
from backend.documents.latex import cv_body
from backend.documents.models import CvChange, ModelOption, TailoredCv
from backend.documents.service import DocumentService
from backend.documents.storage import GenerationStore
from backend.jobs.classification.models import input_hash
from backend.mail.classifier import MailVerdict
from backend.mail.linkedin import JobPageError
from backend.mail.mailbox import MailHeader
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
            cover_letter_paragraphs=[
                "My projects match what this role asks for.",
                "This example cover letter was written by the demo, not by a model.",
            ],
            motivation_letter_paragraphs=[
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
    size = f"Source length: {len(source)} characters."  # Shows that an edit was compiled.
    path.with_suffix(".synctex.gz").write_bytes(b"demo")
    return example_pdf(path.stem, ["Example document produced by demo mode.", size])


def fake_source_line(synctex, page, x, y):
    return 1 + int(y / 20)


class DemoMailbox:
    """A fixed inbox with the same shape as the real one, so the mail pages can be tried."""

    validity = "1"

    def __init__(self):
        self.messages = demo_messages()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def new_headers(self, *, after_uid=None, since=None):
        return [item[0] for item in self.messages if item[0].uid > (after_uid or 0)]

    def body(self, uid):
        return next(item[1] for item in self.messages if item[0].uid == uid)


def demo_messages():
    now = datetime.now(UTC)
    alert = """Your job alert for software engineer in Paris

Junior Backend Engineer
Example Labs · Paris, France
View job: https://www.linkedin.com/comm/jobs/view/1000000001/?trackingId=abc

---------------------------------------------------------
Graduate Data Engineer
Northwind · Lyon, France
View job: https://www.linkedin.com/comm/jobs/view/1000000002/?trackingId=abc

---------------------------------------------------------
Software Engineer, Platform
Contoso · Remote
View job: https://www.linkedin.com/comm/jobs/view/1000000003/?trackingId=abc
"""
    rows = [
        ("no-reply@greenhouse-mail.io", "Thanks for applying to Example Labs", 6, ""),
        ("jobalerts-noreply@linkedin.com", "3 new jobs for software engineer", 5, alert),
        ("talent@northwind.example", "Your application to Northwind", 4, ""),
        ("no-reply@greenhouse-mail.io", "Interview with Example Labs", 2, ""),
        ("hello@newsletter.example", "This week's best deals", 1, ""),
        ("talent@northwind.example", "Your application to Northwind: an update", 1, ""),
    ]
    return [
        (
            MailHeader(
                uid, f"<{uid}@demo>", sender, subject, (now - timedelta(days=days)).isoformat()
            ),
            (body, ""),
        )
        for uid, (sender, subject, days, body) in enumerate(rows, start=1)
    ]


class DemoClassifier:
    def __init__(self, client):
        pass

    def __call__(self, sender, subject, body):
        company = "Example Labs" if "Example Labs" in subject else "Northwind"
        role = "Junior Backend Engineer" if company == "Example Labs" else "Graduate Data Engineer"
        status = "applied"
        if "Interview" in subject:
            status = "interview"
        elif "update" in subject:
            status = "rejected"
        return MailVerdict(is_application=True, company=company, role=role, status=status)


def fake_fetch_job(job_id):
    if job_id == "1000000003":  # Shows what happens when LinkedIn refuses.
        raise JobPageError("LinkedIn would not show this job right now.")
    return {"description": "Build and run backend services with Python, SQL and AWS."}


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
        store,
        fake_generate,
        [model.id for model in settings.document_models],
        compile_document=fake_compile,
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
    table("mail", ["user_id", "item_id"])
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

    profile = json.loads((ROOT / "data/fake-data/demo_profiles.json").read_text())[0]
    profile = CandidateProfile.model_validate({**profile, "user_id": user_id})
    db.Table("profiles").put_item(Item=profile.model_dump(mode="json"))

    s3 = session.client("s3")
    s3.create_bucket(Bucket="documents")
    s3.put_bucket_versioning(Bucket="documents", VersioningConfiguration={"Status": "Enabled"})
    s3.put_object(
        Bucket="documents",
        Key=f"users/{user_id}/cvs/{uuid.uuid4().hex}/example_cv.tex",
        Body=(ROOT / "data/cv-examples/jake_cv.tex").read_bytes(),
    )
    # Tracking began before the demo inbox's messages, which all count as new.
    session.resource("dynamodb").Table("mail").put_item(
        Item={
            "user_id": user_id,
            "item_id": "sync",
            "tracking_started_at": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
            "uid_validity": DemoMailbox.validity,
            "last_uid": 0,
        }
    )
    # Job posts opened earlier: the first matches an email, and the second has none yet.
    now = datetime.now(UTC)
    mail = session.resource("dynamodb").Table("mail")
    for index, (title, company, *_) in enumerate(JOBS[:2]):
        mail.put_item(
            Item={
                "user_id": user_id,
                "item_id": f"interest#demo-{index}",
                "job_id": f"demo-{index}",
                "title": title,
                "company": company,
                "url": "https://example.com",
                "opened_at": (now - timedelta(days=8 if index == 0 else 2)).isoformat(),
            }
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
            document_models=(
                ModelOption(id="demo-fast", label="Demo fast model"),
                ModelOption(id="demo-strong", label="Demo strong model"),
            ),
            compile_document=fake_compile,
            find_source_line=fake_source_line,
            mail_table="mail",
            open_mailbox=DemoMailbox,
            classify_mail=DemoClassifier,
            fetch_job=fake_fetch_job,
            demo=True,
        ),
        region=REGION,
    )
    threading.Thread(target=run_worker, args=(context,), daemon=True).start()
    return context
