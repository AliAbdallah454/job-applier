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
from backend.documents.models import ModelOption, TailoredCv
from backend.documents.service import DocumentService
from backend.documents.storage import GenerationStore
from backend.jobs.classification.models import input_hash

ROOT = Path(__file__).resolve().parents[2]
CV = (ROOT / "data/cv-examples/jake_cv.tex").read_bytes()
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
        self.find_line = Mock()
        self.compiler = Mock(return_value=example_pdf("edited", []))
        self.settings = Settings(
            user_id="ali",
            jobs_table="jobs",
            classifications_table="classifications",
            profiles_table="profiles",
            generations_table="generations",
            documents_bucket="documents",
            document_queue_url=self.queue,
            document_models=(
                ModelOption(id="test-model", label="Test model"),
                ModelOption(id="other-model", label="Other model"),
            ),
            compile_document=self.compiler,
            find_source_line=self.find_line,
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
            ["test-model", "other-model"],
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
            [
                "changes_json",
                "cover_letter_pdf",
                "cover_letter_tex",
                "cv_pdf",
                "cv_tex",
                "motivation_letter_pdf",
                "motivation_letter_tex",
            ],
        )
        self.assertFalse(body["retryable"])

    def test_tailoring_uses_the_chosen_model_and_rejects_unknown_ones(self):
        self.assertEqual(
            [model["id"] for model in self.client.get("/api/options").json()["models"]],
            ["test-model", "other-model"],
        )
        self.assertEqual(self.tailor().json()["model_id"], "test-model")
        cv_id = self.client.get("/api/cvs").json()[0]["cv_id"]
        request = {"job_id": "job-1", "cv_id": cv_id, "model_id": "other-model"}
        chosen = self.client.post("/api/generations", json=request).json()
        self.assertEqual(chosen["model_id"], "other-model")
        self.assertEqual(self.process(chosen["generation_id"]), "ready")
        again = self.client.post(f"/api/generations/{chosen['generation_id']}/regenerate").json()
        self.assertEqual(again["model_id"], "other-model")
        unknown = self.client.post("/api/generations", json={**request, "model_id": "gpt"})
        self.assertEqual(unknown.status_code, 422)

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

    def test_downloads_are_named_after_the_job(self):
        generation_id = self.tailor().json()["generation_id"]
        self.process(generation_id)
        base = f"/api/generations/{generation_id}/files"
        pdf = self.client.get(f"{base}/cv_pdf?download=true")
        self.assertIn('filename="engineer-job-1-cv.pdf"', pdf.headers["content-disposition"])
        self.db.Table("generations").update_item(
            Key={"user_id": "ali", "generation_id": generation_id},
            UpdateExpression="SET job_title = :title",
            ExpressionAttributeValues={":title": 'Ingénieur "\r\n'},
        )
        quoted = self.client.get(f"{base}/cv_pdf?download=true")
        self.assertIn('filename="ing-nieur-cv.pdf"', quoted.headers["content-disposition"])

    def test_history_lists_downloads_and_the_job_link(self):
        generation_id = self.tailor().json()["generation_id"]
        self.db.Table("jobs").update_item(
            Key={"job_id": "job-1"},
            UpdateExpression="SET posting_url = :url",
            ExpressionAttributeValues={":url": "https://example.com/job"},
        )
        waiting = self.client.get("/api/generations").json()[0]
        self.assertEqual(waiting["files"], [])
        self.assertEqual(waiting["company"], "Example")
        self.assertEqual(waiting["job_url"], "https://example.com/job")

        self.process(generation_id)
        ready = self.client.get("/api/generations").json()[0]
        self.assertIn("cv_pdf", ready["files"])
        self.assertIn("motivation_letter_pdf", ready["files"])
        detail = self.client.get(f"/api/generations/{generation_id}").json()
        self.assertEqual(detail["job_url"], "https://example.com/job")

    def test_history_survives_a_removed_job_and_refuses_non_web_links(self):
        generation_id = self.tailor().json()["generation_id"]
        self.db.Table("jobs").update_item(
            Key={"job_id": "job-1"},
            UpdateExpression="SET application_url = :url",
            ExpressionAttributeValues={":url": "javascript:alert(1)"},
        )
        self.assertIsNone(self.client.get("/api/generations").json()[0]["job_url"])
        self.db.Table("jobs").delete_item(Key={"job_id": "job-1"})
        listed = self.client.get("/api/generations").json()[0]
        self.assertEqual(listed["generation_id"], generation_id)
        self.assertIsNone(listed["job_url"])
        self.assertIsNone(listed["company"])

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

    def edit(self, generation_id, source, revision=None, document="cv"):
        url = f"/api/generations/{generation_id}"
        if revision is None:
            revision = self.client.get(url).json().get("revisions", {}).get(document, 0)
        return self.client.post(
            f"{url}/edit", json={"document": document, "latex": source, "revision": revision}
        )

    def cv_source(self, generation_id):
        return self.client.get(f"/api/generations/{generation_id}/files/cv_tex").text

    def version_file(self, generation_id, number, name="cv_tex"):
        return self.client.get(
            f"/api/generations/{generation_id}/versions/cv/{number}/files/{name}"
        )

    def added_by_hand(self, generation_id, line):
        source = self.cv_source(generation_id)
        return source.replace("\\end{document}", f"{line}\n\\end{{document}}")

    def test_editing_updates_the_generation_in_place_without_the_queue_or_model(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        queued = self.messages()
        before = self.context.generations.get("ali", original)
        source = self.added_by_hand(original, "Added by hand")
        response = self.edit(original, source)
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["generation_id"], original)
        self.assertNotIn("edited_from", detail)
        self.assertEqual(detail["status"], "ready")
        self.assertEqual(detail["created_at"], before["created_at"])
        self.assertGreater(detail["updated_at"], before["updated_at"])
        self.compiler.assert_called_once()
        self.assertEqual(self.messages(), [])
        self.assertEqual(len(queued), 1)

        self.assertEqual(self.cv_source(original), source)
        self.assertIn("Added by hand", detail["changes"][-1]["after"])
        self.assertEqual(len(detail["explanations"]), 2)
        self.assertEqual(detail["keywords"][0], "Python")
        self.assertEqual(
            detail["files"],
            [
                "changes_json",
                "cover_letter_pdf",
                "cover_letter_tex",
                "cv_pdf",
                "cv_tex",
                "motivation_letter_pdf",
                "motivation_letter_tex",
            ],
        )
        after = self.context.generations.get("ali", original)
        self.assertEqual(after["job_created"], before["job_created"])
        for name in ("motivation_letter_pdf", "motivation_letter_tex"):
            self.assertEqual(after["artifacts"][name], before["artifacts"][name])
        self.assertNotEqual(after["artifacts"]["cv_pdf"], before["artifacts"]["cv_pdf"])

    def test_two_edits_leave_one_entry_for_the_job_and_two_previous_versions(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        ready = self.context.generations.get("ali", original)
        model_source = self.cv_source(original)
        model_pdf = self.client.get(f"/api/generations/{original}/files/cv_pdf").content
        self.compiler.return_value = b"%PDF-first"
        first = self.added_by_hand(original, "First edit")
        saved_first = self.edit(original, first).json()
        self.compiler.return_value = b"%PDF-second"
        second = self.added_by_hand(original, "Second edit")
        detail = self.edit(original, second).json()

        for listing in ("/api/generations", "/api/generations?job_id=job-1"):
            rows = self.client.get(listing).json()
            self.assertEqual([row["generation_id"] for row in rows], [original])
        self.assertEqual(
            detail["previous_versions"],
            [
                {"document": "cv", "number": 1, "saved_at": ready["updated_at"]},
                {"document": "cv", "number": 2, "saved_at": saved_first["updated_at"]},
            ],
        )
        fetched = self.client.get(f"/api/generations/{original}").json()
        self.assertEqual(fetched["previous_versions"], detail["previous_versions"])

        self.assertEqual(self.version_file(original, 1).text, model_source)
        self.assertEqual(self.version_file(original, 1, "cv_pdf").content, model_pdf)
        self.assertEqual(self.version_file(original, 2).text, first)
        self.assertEqual(self.version_file(original, 2, "cv_pdf").content, b"%PDF-first")
        changes = self.version_file(original, 2, "changes_json").json()
        self.assertIn("First edit", changes["diff"][-1]["after"])
        self.assertNotIn("First edit", self.version_file(original, 1, "changes_json").text)

        self.assertEqual(self.cv_source(original), second)
        current = self.client.get(f"/api/generations/{original}/files/cv_pdf")
        self.assertEqual(current.content, b"%PDF-second")
        letter = self.context.generations.get("ali", original)["artifacts"]
        self.assertEqual(
            letter["motivation_letter_pdf"], ready["artifacts"]["motivation_letter_pdf"]
        )

    def test_a_version_file_can_be_downloaded_under_the_job_name(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        self.edit(original, self.added_by_hand(original, "Edit"))
        response = self.client.get(
            f"/api/generations/{original}/versions/cv/1/files/cv_tex?download=true"
        )
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="engineer-job-1-cv.tex"',
        )
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_a_position_in_the_pdf_finds_its_source_line(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        url = f"/api/generations/{original}/source-line?page=1&x=20.5&y=30"
        self.assertIn("position data", self.client.get(url).json()["detail"])

        def compile_with_positions(path):
            path.with_suffix(".synctex.gz").write_bytes(b"positions")
            return example_pdf("x", [])

        self.compiler.side_effect = compile_with_positions
        self.edit(original, self.added_by_hand(original, "Edit"))
        self.find_line.return_value = None
        self.assertEqual(self.client.get(url).status_code, 404)
        self.find_line.return_value = 12
        self.assertEqual(self.client.get(url).json(), {"line": 12})
        self.find_line.assert_called_with(b"positions", 1, 20.5, 30.0)

    def test_an_edit_that_does_not_compile_returns_the_error_and_saves_nothing(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        before = self.context.generations.get("ali", original)
        self.compiler.side_effect = DocumentCompilationError(
            timed_out=False, diagnostics=b"noise\n! Undefined control sequence."
        )
        response = self.edit(original, self.cv_source(original))
        self.assertEqual(response.status_code, 422)
        self.assertIn("! Undefined control sequence.", response.json()["detail"])
        self.assertNotIn("noise", response.json()["detail"])
        self.assertEqual(len(self.client.get("/api/generations").json()), 1)
        self.assertEqual(self.context.generations.get("ali", original), before)

    def test_a_save_that_lost_a_race_gets_409_and_loses_no_version(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        store = self.context.generations
        model_source = self.cv_source(original)
        rival = self.added_by_hand(original, "Saved in another tab")

        def compile_while_another_tab_saves(path):
            store.save_edit(store.get("ali", original), rival, b"%PDF-rival")
            return b"%PDF-mine"

        self.compiler.side_effect = compile_while_another_tab_saves
        response = self.edit(original, self.added_by_hand(original, "Mine"))
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed elsewhere", response.json()["detail"])
        self.assertIn("Save again", response.json()["detail"])

        detail = self.client.get(f"/api/generations/{original}").json()
        self.assertEqual([item["number"] for item in detail["previous_versions"]], [1])
        self.assertEqual(self.version_file(original, 1).text, model_source)
        self.assertEqual(self.cv_source(original), rival)

        self.compiler.side_effect = None
        mine = self.added_by_hand(original, "Mine")
        self.assertEqual(self.edit(original, mine).status_code, 200)
        self.assertEqual(self.version_file(original, 2).text, rival)
        self.assertEqual(self.cv_source(original), mine)

    def letter(self, generation_id, name):
        return self.client.get(f"/api/generations/{generation_id}/files/{name}_tex").text

    def test_a_letter_edit_replaces_that_letter_and_keeps_its_own_history(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        before = self.client.get(f"/api/generations/{original}").json()
        self.assertEqual(before["revisions"], {"cv": 0, "motivation_letter": 0, "cover_letter": 0})
        model_letter = self.letter(original, "cover_letter")
        untouched = self.letter(original, "motivation_letter")
        edited = model_letter.replace("\\begin{document}", "\\begin{document}\nBy hand\n")

        detail = self.edit(original, edited, document="cover_letter").json()

        self.assertEqual(self.letter(original, "cover_letter"), edited)
        self.assertEqual(self.letter(original, "motivation_letter"), untouched)
        self.assertEqual(detail["revisions"]["cover_letter"], 1)
        self.assertEqual(detail["revisions"]["cv"], 0)
        self.assertEqual(detail["changes"], before["changes"])
        self.assertEqual(
            detail["previous_versions"],
            [{"document": "cover_letter", "number": 1, "saved_at": before["updated_at"]}],
        )
        old = self.client.get(
            f"/api/generations/{original}/versions/cover_letter/1/files/cover_letter_tex"
        )
        self.assertEqual(old.text, model_letter)
        self.assertEqual(self.version_file(original, 1).status_code, 404)

    def test_saving_one_document_leaves_a_draft_of_another_valid(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        letter = self.letter(original, "motivation_letter")
        self.edit(original, self.added_by_hand(original, "Edit"))

        response = self.edit(original, letter + "%", 0, "motivation_letter")

        self.assertEqual(response.status_code, 200)
        stale = self.edit(original, letter + "%%", 0, "motivation_letter")
        self.assertEqual(stale.status_code, 409)

    def test_a_generation_from_before_cover_letters_has_none_to_edit(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        self.context.generations.table.update_item(
            Key={"user_id": "ali", "generation_id": original},
            UpdateExpression="REMOVE artifacts.cover_letter_tex, artifacts.cover_letter_pdf",
        )
        detail = self.client.get(f"/api/generations/{original}").json()
        self.assertNotIn("cover_letter", detail["revisions"])
        response = self.edit(original, CV.decode(), 0, "cover_letter")
        self.assertEqual(response.status_code, 404)

    def test_a_draft_from_before_another_save_gets_409_and_changes_nothing(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        loaded = self.client.get(f"/api/generations/{original}").json()["revisions"]["cv"]
        self.edit(original, self.added_by_hand(original, "Saved in another tab"))
        saved = self.context.generations.get("ali", original)
        self.compiler.reset_mock()

        response = self.edit(original, self.added_by_hand(original, "Stale draft"), loaded)
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed elsewhere", response.json()["detail"])
        self.compiler.assert_not_called()
        self.assertEqual(self.context.generations.get("ali", original), saved)

    def test_an_edit_must_say_which_state_it_started_from(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        response = self.client.post(
            f"/api/generations/{original}/edit",
            json={"document": "cv", "latex": self.cv_source(original)},
        )
        self.assertEqual(response.status_code, 422)

    def test_a_row_from_the_old_edit_flow_says_its_first_cv_is_not_the_models(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        url = f"/api/generations/{original}"
        self.assertFalse(self.client.get(url).json()["starts_from_edit"])
        self.context.generations.table.update_item(
            Key={"user_id": "ali", "generation_id": original},
            UpdateExpression="SET edited_from = :source",
            ExpressionAttributeValues={":source": "0" * 32},
        )
        detail = self.client.get(url).json()
        self.assertTrue(detail["starts_from_edit"])
        self.assertNotIn("edited_from", detail)
        edited = self.edit(original, self.added_by_hand(original, "Edit")).json()
        self.assertTrue(edited["starts_from_edit"])

    def test_a_version_file_needs_a_real_number_and_name(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        self.assertEqual(self.version_file(original, 1).status_code, 404)
        self.edit(original, self.added_by_hand(original, "First"))
        self.edit(original, self.added_by_hand(original, "Second"))
        self.assertEqual(self.version_file(original, 2).status_code, 200)
        for number in (0, -1, -2, 3, 10**30):
            self.assertEqual(self.version_file(original, number).status_code, 404, number)
        for name in ("saved_at", "files", "motivation_letter_pdf", "input", "nothing"):
            self.assertEqual(self.version_file(original, 1, name).status_code, 404, name)
        self.assertEqual(self.version_file("0" * 64, 1).status_code, 404)

    def test_version_files_of_other_users_are_invisible(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        self.edit(original, self.added_by_hand(original, "Mine"))
        other = Context(
            Settings(**{**self.settings.__dict__, "user_id": "other"}), region="us-east-1"
        )
        client = TestClient(create_app(other), base_url="http://localhost")
        url = f"/api/generations/{original}/versions/cv/1/files/cv_tex"
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(client.get(url).status_code, 404)

    def test_only_old_versions_may_be_cached_because_an_edit_changes_the_current_file(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        self.edit(original, self.added_by_hand(original, "Edit"))
        for name in ("cv_pdf", "cv_tex", "motivation_letter_pdf"):
            response = self.client.get(f"/api/generations/{original}/files/{name}?v=anything")
            self.assertEqual(response.headers["cache-control"], "private, no-cache", name)
        response = self.version_file(original, 1, "cv_pdf")
        self.assertEqual(response.headers["cache-control"], "private, max-age=3600")

    def test_regenerating_an_edited_cv_starts_again_from_the_frozen_input(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        self.edit(original, self.added_by_hand(original, "Added by hand"))
        body = self.client.post(f"/api/generations/{original}/regenerate").json()
        self.assertNotEqual(body["generation_id"], original)
        record = self.context.generations.get("ali", body["generation_id"])
        self.assertNotIn("cv_versions", record)
        frozen = json.loads(self.context.generations.read_file(record["input"]))
        self.assertNotIn("Added by hand", frozen["cv_latex"])
        self.assertEqual(len(self.client.get("/api/generations").json()), 2)

    def test_editing_without_tex_says_what_is_missing(self):
        original = self.tailor().json()["generation_id"]
        self.process(original)
        self.compiler.side_effect = RuntimeError("pdflatex is required")
        response = self.edit(original, self.cv_source(original))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "pdflatex is required")

    def test_only_a_complete_cv_of_a_ready_generation_can_be_edited(self):
        original = self.tailor().json()["generation_id"]
        self.assertEqual(self.edit(original, CV.decode()).status_code, 409)
        self.process(original)
        self.assertEqual(self.edit(original, "no document here").status_code, 400)
        self.assertEqual(self.edit(original, "").status_code, 422)
        self.assertEqual(self.edit("0" * 64, CV.decode()).status_code, 404)
        self.compiler.assert_not_called()

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
