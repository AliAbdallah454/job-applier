"""Exercise the HTTP API against Moto; the queue is inspected, never consumed by a model."""

import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import boto3
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws

from backend.api.app import create_app
from backend.api.demo import create_tables, example_pdf, fake_generate
from backend.api.deps import Context, Settings
from backend.documents.errors import DocumentCompilationError
from backend.documents.models import TailoredCv
from backend.documents.service import DocumentService
from backend.documents.storage import GenerationStore
from backend.jobs.classification.models import input_hash

ROOT = Path(__file__).resolve().parents[2]
CV = (ROOT / "backend/documents/examples/jake_cv.tex").read_bytes()
PROFILE = {"user_id": "ali", "full_name": "Ali Example"}


@mock_aws
class ApiTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        self.session = boto3.Session(region_name="us-east-1")
        self.db = self.session.resource("dynamodb")
        create_tables(self.db)
        self.s3 = self.session.client("s3")
        self.s3.create_bucket(Bucket="documents")
        self.s3.put_bucket_versioning(
            Bucket="documents", VersioningConfiguration={"Status": "Enabled"}
        )
        self.sqs = self.session.client("sqs")
        self.queue = self.sqs.create_queue(QueueName="documents")["QueueUrl"]
        self.settings = Settings(
            user_id="ali",
            jobs_table="jobs",
            classifications_table="classifications",
            profiles_table="profiles",
            generations_table="generations",
            documents_bucket="documents",
            document_queue_url=self.queue,
            document_model_id="test-model",
        )
        self.context = Context(self.settings, region="us-east-1")
        self.client = TestClient(create_app(self.context), base_url="http://localhost")
        self.add_job("job-1", "junior")

    def add_job(self, job_id, seniority):
        state = {"title": f"Engineer {job_id}", "description": "Build Python services"}
        self.db.Table("jobs").put_item(Item={"job_id": job_id, **state, "company": "Example"})
        self.db.Table("classifications").put_item(
            Item={
                "job_id": job_id,
                "seniority": seniority,
                "contract_type": "permanent",
                "is_tech": True,
                "category": "software_engineer",
                "tech_status": "tech",
                "seniority_job": f"{seniority}#{job_id}",
                "input_hash": input_hash(state),
            }
        )

    def upload(self, name="cv.tex", body=CV):
        return self.client.post("/api/cvs", files={"file": (name, io.BytesIO(body))})

    def tailor(self):
        self.client.put("/api/profile", json=PROFILE)
        cv_id = self.upload().json()["cv_id"]
        return self.client.post("/api/generations", json={"job_id": "job-1", "cv_id": cv_id})

    def messages(self):
        response = self.sqs.receive_message(QueueUrl=self.queue, MaxNumberOfMessages=10)
        return [json.loads(message["Body"]) for message in response.get("Messages", [])]

    def process(self, generation_id, compile_document=None):
        store = GenerationStore(self.db.Table("generations"), self.s3, "documents")
        service = DocumentService(
            store,
            fake_generate_now,
            "test-model",
            compile_document=compile_document or (lambda path: example_pdf("x", [])),
        )
        return service.process({"user_id": "ali", "generation_id": generation_id})

    def test_options_name_the_user_and_taxonomy(self):
        body = self.client.get("/api/options").json()
        self.assertEqual(body["user_id"], "ali")
        self.assertEqual(body["seniorities"], ["entry", "junior", "mid", "senior"])
        self.assertFalse(body["demo"])

    def test_jobs_filter_and_invalid_values_return_400(self):
        self.add_job("job-2", "senior")
        body = self.client.get("/api/jobs").json()
        self.assertEqual([job["job_id"] for job in body["jobs"]], ["job-1"])
        body = self.client.get("/api/jobs", params={"seniority": ""}).json()
        self.assertEqual(len(body["jobs"]), 2)
        self.assertEqual(self.client.get("/api/jobs?seniority=lead").status_code, 400)
        self.assertEqual(self.client.get("/api/jobs?cursor=nonsense").status_code, 400)

    def test_job_detail_hides_missing_and_stale_jobs(self):
        self.assertEqual(self.client.get("/api/jobs/job-1").json()["title"], "Engineer job-1")
        self.assertEqual(self.client.get("/api/jobs/missing").status_code, 404)
        self.db.Table("jobs").update_item(
            Key={"job_id": "job-1"},
            UpdateExpression="SET description = :new",
            ExpressionAttributeValues={":new": "Changed duties"},
        )
        self.assertEqual(self.client.get("/api/jobs/job-1").status_code, 404)

    def test_profile_is_created_versioned_and_guarded_against_stale_saves(self):
        self.assertEqual(self.client.get("/api/profile").status_code, 404)
        created = self.client.put("/api/profile", json=PROFILE).json()
        self.assertEqual(created["version"], 1)
        updated = self.client.put("/api/profile", json={**created, "location": "Paris"}).json()
        self.assertEqual(updated["version"], 2)
        self.assertEqual(self.client.get("/api/profile").json()["location"], "Paris")
        stale = self.client.put("/api/profile", json=created)
        self.assertEqual(stale.status_code, 409)

    def test_profile_without_a_stored_version_can_still_be_saved(self):
        self.db.Table("profiles").put_item(Item=PROFILE)
        loaded = self.client.get("/api/profile").json()
        saved = self.client.put("/api/profile", json=loaded)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["version"], 2)

    def test_profile_rejects_other_users_and_invalid_sections(self):
        other = self.client.put("/api/profile", json={**PROFILE, "user_id": "someone"})
        self.assertEqual(other.status_code, 403)
        invalid = self.client.put(
            "/api/profile",
            json={
                **PROFILE,
                "experiences": [
                    {"organization": "A", "role": "B", "description": "C", "start_date": "2024-13"}
                ],
            },
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(
            invalid.json()["detail"][0]["loc"][:4], ["body", "experiences", 0, "start_date"]
        )

    def test_cv_upload_list_and_delete(self):
        cv = self.upload("my cv.tex").json()
        self.assertEqual(cv["filename"], "my_cv.tex")
        self.assertEqual(cv["size"], len(CV))
        self.assertEqual(
            [item["cv_id"] for item in self.client.get("/api/cvs").json()], [cv["cv_id"]]
        )
        self.assertEqual(self.client.delete(f"/api/cvs/{cv['cv_id']}").status_code, 204)
        self.assertEqual(self.client.get("/api/cvs").json(), [])
        self.assertEqual(self.client.delete(f"/api/cvs/{cv['cv_id']}").status_code, 404)

    def test_cv_upload_rejects_bad_files(self):
        self.assertEqual(self.upload("cv.pdf").status_code, 400)
        self.assertEqual(self.upload(body=b"no document here").status_code, 400)
        self.assertEqual(self.upload(body=b"\xff\xfe").status_code, 400)
        self.assertEqual(self.upload(body=b"x" * 200_001).status_code, 400)
        self.assertEqual(self.client.get("/api/cvs").json(), [])

    def test_tailor_button_flow_queues_one_message_for_the_worker(self):
        response = self.tailor()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["job_title"], "Engineer job-1")
        self.assertEqual(
            self.messages(), [{"user_id": "ali", "generation_id": body["generation_id"]}]
        )

    def test_tailoring_needs_a_profile_job_and_cv(self):
        cv_id = self.upload().json()["cv_id"]
        request = {"job_id": "job-1", "cv_id": cv_id}
        self.assertEqual(self.client.post("/api/generations", json=request).status_code, 409)
        self.client.put("/api/profile", json=PROFILE)
        missing_job = self.client.post("/api/generations", json={**request, "job_id": "none"})
        self.assertEqual(missing_job.status_code, 404)
        missing_cv = self.client.post("/api/generations", json={**request, "cv_id": "0" * 32})
        self.assertEqual(missing_cv.status_code, 404)
        self.assertEqual(self.messages(), [])

    def test_ready_generation_returns_review_list_keywords_and_files(self):
        generation_id = self.tailor().json()["generation_id"]
        self.assertEqual(self.process(generation_id), "ready")
        body = self.client.get(f"/api/generations/{generation_id}").json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["keywords"][0], "Python")
        self.assertIn("bounded retries so APIs", body["changes"][0]["after"])
        self.assertEqual(len(body["explanations"]), 2)
        self.assertTrue(body["explanations"][0]["reason"])
        self.assertEqual(
            body["files"],
            ["changes_json", "cv_pdf", "cv_tex", "motivation_letter_pdf", "motivation_letter_tex"],
        )
        self.assertFalse(body["retryable"])

    def test_generation_from_before_the_rewrite_still_loads(self):
        generation_id = self.tailor().json()["generation_id"]
        self.process(generation_id)
        store = GenerationStore(self.db.Table("generations"), self.s3, "documents")
        reference = store.get("ali", generation_id)["artifacts"]["changes_json"]
        self.s3.put_object(
            Bucket="documents",
            Key=reference["key"],
            Body=json.dumps([{"find": "a", "replace": "b", "reason": "c", "applied": True}]),
        )
        self.db.Table("generations").update_item(
            Key={"user_id": "ali", "generation_id": generation_id},
            UpdateExpression="REMOVE artifacts.changes_json.version_id",
        )
        body = self.client.get(f"/api/generations/{generation_id}").json()
        self.assertEqual((body["changes"], body["explanations"]), ([], []))

    def test_files_are_served_inline_or_as_downloads_and_unknown_names_404(self):
        generation_id = self.tailor().json()["generation_id"]
        base = f"/api/generations/{generation_id}/files"
        self.assertEqual(self.client.get(f"{base}/cv_pdf").status_code, 404)
        self.process(generation_id)
        pdf = self.client.get(f"{base}/cv_pdf")
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        self.assertIn("inline", pdf.headers["content-disposition"])
        source = self.client.get(f"{base}/cv_tex?download=true")
        self.assertIn("attachment", source.headers["content-disposition"])
        self.assertTrue(source.headers["content-type"].startswith("text/plain"))
        self.assertEqual(self.client.get(f"{base}/input").status_code, 404)

    def test_failed_compile_shows_the_log_and_retry_requeues(self):
        generation_id = self.tailor().json()["generation_id"]
        self.messages()
        failure = DocumentCompilationError(timed_out=False, diagnostics=b"Undefined control")
        with self.assertRaises(DocumentCompilationError):
            self.process(generation_id, Mock(side_effect=failure))
        body = self.client.get(f"/api/generations/{generation_id}").json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["failure_stage"], "compiling_pdf")
        self.assertIn("Undefined control", body["compilation_log"])
        self.assertTrue(body["retryable"])
        retried = self.client.post(f"/api/generations/{generation_id}/retry")
        self.assertEqual(retried.status_code, 202)

    def test_retry_is_refused_unless_failed_or_abandoned(self):
        generation_id = self.tailor().json()["generation_id"]
        url = f"/api/generations/{generation_id}/retry"
        self.assertEqual(self.client.post(url).status_code, 409)
        table = self.db.Table("generations")
        key = {"user_id": "ali", "generation_id": generation_id}
        table.update_item(
            Key=key,
            UpdateExpression="SET #s = :s, lease_until = :old",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "processing", ":old": 1},
        )
        self.assertEqual(self.client.post(url).status_code, 202)

    def test_regenerate_makes_a_new_revision_from_the_frozen_input(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        self.client.put(
            "/api/profile",
            json={**self.client.get("/api/profile").json(), "location": "Changed later"},
        )
        body = self.client.post(f"/api/generations/{original}/regenerate").json()
        self.assertNotEqual(body["generation_id"], original)
        self.assertEqual(body["status"], "queued")
        record = self.context.generations.get("ali", body["generation_id"])
        frozen = json.loads(self.context.generations.read_file(record["input"]))
        self.assertNotIn("location", {k for k, v in frozen["profile"].items() if v})

    def test_history_lists_newest_first_and_filters(self):
        first = self.tailor().json()["generation_id"]
        second = self.client.post(f"/api/generations/{first}/regenerate").json()["generation_id"]
        self.process(first)
        ids = [item["generation_id"] for item in self.client.get("/api/generations").json()]
        self.assertEqual(ids, [second, first])
        ready = self.client.get("/api/generations?status=ready").json()
        self.assertEqual([item["generation_id"] for item in ready], [first])
        by_job = self.client.get("/api/generations?job_id=job-1").json()
        self.assertEqual(len(by_job), 2)
        self.assertEqual(self.client.get("/api/generations?job_id=other").json(), [])
        self.assertEqual(self.client.get("/api/generations?status=lost").status_code, 422)

    def test_other_users_generations_are_invisible(self):
        generation_id = self.tailor().json()["generation_id"]
        other = Context(
            Settings(**{**self.settings.__dict__, "user_id": "other"}), region="us-east-1"
        )
        client = TestClient(create_app(other), base_url="http://localhost")
        self.assertEqual(client.get(f"/api/generations/{generation_id}").status_code, 404)
        self.assertEqual(client.get("/api/generations").json(), [])
        self.assertEqual(client.get("/api/cvs").json(), [])

    def test_cross_site_writes_and_foreign_hosts_are_refused(self):
        refused = self.client.put(
            "/api/profile", json=PROFILE, headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(refused.status_code, 403)
        allowed = self.client.put(
            "/api/profile", json=PROFILE, headers={"Origin": "http://localhost:5173"}
        )
        self.assertEqual(allowed.status_code, 200)
        foreign = TestClient(create_app(self.context), base_url="http://evil.example")
        self.assertEqual(foreign.get("/api/options").status_code, 400)

    def test_aws_failures_are_useful_without_internal_details(self):
        error = ClientError({"Error": {"Code": "Denied", "Message": "private arn"}}, "Query")
        client = TestClient(
            create_app(self.context), base_url="http://localhost", raise_server_exceptions=False
        )
        with (
            patch("backend.api.catalogue.JobCatalogue.page", Mock(side_effect=error)),
            patch("backend.api.app.LOG.error"),
        ):
            response = client.get("/api/jobs")
        self.assertEqual(response.status_code, 503)
        self.assertIn("AWS login", response.json()["detail"])
        self.assertNotIn("private", response.text)

    def test_openapi_schema_builds_without_aws(self):
        paths = create_app().openapi()["paths"]
        self.assertIn("/api/generations/{generation_id}/regenerate", paths)


def fake_generate_now(request, previous=None) -> tuple[TailoredCv, dict]:
    with patch("backend.api.demo.time.sleep"):
        return fake_generate(request, previous)
