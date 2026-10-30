"""Offline generation, retry and rendering tests; no paid model calls."""

import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import boto3
from botocore.stub import Stubber
from moto import mock_aws

from backend.documents.__main__ import main as cli_main
from backend.documents.bedrock import BedrockGenerator, tool_schema
from backend.documents.errors import DocumentCompilationError, DocumentError
from backend.documents.latex import apply_edits, engine, escape, validate_cv
from backend.documents.models import CvEdit, GenerationInput, Job, TailoredCv
from backend.documents.render import compile_pdf, render_sources
from backend.documents.service import DocumentService
from backend.documents.storage import GenerationStore
from backend.profiles.models import CandidateProfile

ROOT = Path(__file__).resolve().parents[2]


CV = (ROOT / "backend/documents/examples/jake_cv.tex").read_text()


def request():
    profile = CandidateProfile.model_validate(
        json.loads((ROOT / "scripts/fixtures/demo_profiles.json").read_text())[0]
    )
    return GenerationInput(
        profile=profile,
        job=Job(job_id="job-1", title="Python engineer", description="Build APIs"),
        model_id="us.amazon.nova-pro-v1:0",
        cv_latex=CV,
    )


BULLET = r"\resumeItem{Added bounded retries for intermittent request failures}"


def content():
    return TailoredCv(
        job_keywords=["Python", "APIs", "FastAPI"],
        edits=[
            {
                "find": r"\resumeItem{Built a prediction endpoint for the classifier}",
                "replace": r"\resumeItem{Built a FastAPI prediction endpoint, 50\% \& more}",
                "reason": "FastAPI",
            },
            {"find": BULLET, "replace": "", "reason": "Not relevant"},
            {
                "find": "\\textbf{Languages}{: Python, SQL}",
                "replace": "\\textbf{Languages}{: SQL, Python, FastAPI}",
                "reason": "Skills",
            },
        ],
        letter_paragraphs=["I am interested in the role.", "I built Python tools."],
    )


def edit(find, replace):
    return CvEdit(find=find, replace=replace, reason="test")


class CvEditTests(unittest.TestCase):
    def test_edits_replace_only_their_snippet(self):
        edited, report = apply_edits(CV, content().edits)
        self.assertTrue(all(item["applied"] for item in report))
        self.assertIn(r"\resumeItem{Built a FastAPI prediction endpoint, 50\% \& more}", edited)
        self.assertNotIn("bounded retries", edited)
        self.assertIn(r"\textbf{Languages}{: SQL, Python, FastAPI}", edited)
        self.assertEqual(
            edited.replace(
                r"\resumeItem{Built a FastAPI prediction endpoint, 50\% \& more}", ""
            ).replace(r"\textbf{Languages}{: SQL, Python, FastAPI}", ""),
            CV.replace(BULLET, "")
            .replace(r"\resumeItem{Built a prediction endpoint for the classifier}", "")
            .replace("\\textbf{Languages}{: Python, SQL}", ""),
        )

    def test_whitespace_differences_in_find_are_tolerated(self):
        loose = edit(
            "\\resumeItem{Added bounded   retries\n for intermittent request failures}", ""
        )
        self.assertNotIn("bounded retries", apply_edits(CV, [loose])[0])

    def test_invalid_edits_are_skipped_and_the_rest_applied(self):
        good = edit(r"\resumeItem{Built a prediction endpoint for the classifier}", "")
        for name, bad, reason in [
            ("missing", edit("Not in the CV", "x"), "not in the CV body"),
            ("ambiguous", edit("Python", "Go"), "several places"),
            ("preamble", edit(r"\usepackage{latexsym}", ""), "not in the CV body"),
            ("new command", edit(BULLET, r"\resumeItem{x}\input{/etc/passwd}"), "commands"),
            ("braces", edit(BULLET, r"\resumeItem{x"), "braces"),
            ("comment", edit(BULLET, r"\resumeItem{100% done}"), "%"),
            ("overlap", edit("prediction endpoint", "API"), "overlaps"),
        ]:
            with self.subTest(name):
                edited, report = apply_edits(CV, [good, bad])
                self.assertNotIn("Built a prediction endpoint", edited)
                self.assertEqual([item["applied"] for item in report], [True, False])
                self.assertIn(reason, report[1]["error"])
                if name != "overlap":
                    self.assertEqual(edited, CV.replace(good.find, ""))

    def test_snippet_also_in_the_preamble_matches_the_body_only(self):
        cv = CV.replace(r"\begin{document}", "% Example Analytics Company\n\\begin{document}")
        edited, report = apply_edits(cv, [edit("Example Analytics Company", "Example Co")])
        self.assertTrue(report[0]["applied"])
        self.assertIn("% Example Analytics Company", edited)

    def test_engine_follows_the_cv(self):
        self.assertEqual(engine(CV), "xelatex")
        self.assertEqual(engine(r"\documentclass{article}\input{glyphtounicode}"), "pdflatex")

    def test_incomplete_cv_is_rejected(self):
        with self.assertRaises(DocumentError):
            validate_cv("Just text")


class RenderingTests(unittest.TestCase):
    def test_cv_and_letter_are_escaped(self):
        generated = content()
        generated.letter_paragraphs[0] = r"50% & \input{secret}"
        sources = render_sources(request(), generated)
        letter = sources["motivation_letter.tex"].decode()
        self.assertIn(r"50\% \& \textbackslash{}input\{secret\}", letter)
        self.assertIn(b"FastAPI prediction endpoint", sources["cv.tex"])
        self.assertEqual(escape("$_#"), r"\$\_\#")

    def test_nova_schema_inlines_references_and_uses_supported_root_fields(self):
        schema = tool_schema("us.amazon.nova-pro-v1:0")
        self.assertEqual(set(schema), {"type", "properties", "required"})
        self.assertNotIn('"$ref"', json.dumps(schema))
        self.assertNotIn('"$defs"', json.dumps(schema))
        self.assertEqual(schema["properties"]["edits"]["items"]["type"], "object")
        self.assertIn("find", schema["properties"]["edits"]["items"]["required"])

    def test_claude_override_keeps_existing_schema(self):
        self.assertEqual(
            tool_schema("us.anthropic.claude-sonnet-4-6"), TailoredCv.model_json_schema()
        )

    def test_compiler_missing_is_actionable(self):
        with patch("backend.documents.render.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "xelatex is required"):
                compile_pdf(Path("cv.tex"))

    def test_compiler_failure_preserves_log_without_exposing_it_in_message(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cv.tex"
            source.write_text("test")
            source.with_suffix(".log").write_text("Private source context\n! Missing $ inserted.")
            failure = subprocess.CalledProcessError(1, ["xelatex"], output=b"compiler stdout")
            with (
                patch("backend.documents.render.shutil.which", return_value="xelatex"),
                patch("backend.documents.render.subprocess.run", side_effect=failure),
            ):
                with self.assertRaises(DocumentCompilationError) as caught:
                    compile_pdf(source)
            self.assertIn(b"Missing $ inserted", caught.exception.diagnostics)
            self.assertIn(b"compiler stdout", caught.exception.diagnostics)
            self.assertNotIn("Private source context", str(caught.exception))

    def test_compiler_timeout_preserves_partial_output(self):
        failure = subprocess.TimeoutExpired(["xelatex"], 60, output=b"partial compiler output")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cv.tex"
            with (
                patch("backend.documents.render.shutil.which", return_value="xelatex"),
                patch("backend.documents.render.subprocess.run", side_effect=failure),
            ):
                with self.assertRaisesRegex(DocumentCompilationError, "timed out") as caught:
                    compile_pdf(source)
            self.assertIn(b"partial compiler output", caught.exception.diagnostics)

    def test_compiler_has_no_shell_escape_or_aws_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cv.tex"
            source.write_text("test")
            source.with_suffix(".pdf").write_bytes(b"%PDF-test")
            with (
                patch("backend.documents.render.shutil.which", return_value="/usr/bin/xelatex"),
                patch("backend.documents.render.subprocess.run") as run,
            ):
                self.assertEqual(compile_pdf(source), b"%PDF-test")
            self.assertIn("-no-shell-escape", run.call_args.args[0])
            self.assertNotIn("AWS_ACCESS_KEY_ID", run.call_args.kwargs["env"])
            self.assertEqual(run.call_args.kwargs["env"]["openin_any"], "p")


@mock_aws
class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.session = boto3.Session(region_name="us-east-1")
        table = self.session.resource("dynamodb").create_table(
            TableName="generations",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "generation_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "generation_id", "AttributeType": "S"},
            ],
        )
        s3 = self.session.client("s3")
        s3.create_bucket(Bucket="test-documents")
        s3.put_bucket_versioning(
            Bucket="test-documents", VersioningConfiguration={"Status": "Enabled"}
        )
        self.store = GenerationStore(table, s3, "test-documents")
        self.sqs = self.session.client("sqs")
        self.queue = self.sqs.create_queue(QueueName="documents")["QueueUrl"]
        self.request = request()
        self.generate = Mock(return_value=(content(), {"inputTokens": 50, "outputTokens": 30}))
        self.compiler = Mock(return_value=b"%PDF-test")
        self.service = DocumentService(
            self.store, self.generate, self.request.model_id, compile_document=self.compiler
        )

    def submit(self, **kwargs):
        record = self.store.submit(self.request, self.sqs, self.queue, **kwargs)
        return {"user_id": record["user_id"], "generation_id": record["generation_id"]}

    def test_success_links_user_job_and_versioned_artifacts(self):
        message = self.submit()
        self.assertEqual(self.service.process(message), "ready")
        result = self.store.get(**message)
        self.assertEqual(result["job_id"], "job-1")
        self.assertTrue(result["review_required"])
        self.assertEqual(len(result["artifacts"]), 5)
        changes = json.loads(self.store.read_file(result["artifacts"]["changes_json"]))
        self.assertEqual(
            changes[1],
            {"find": BULLET, "replace": "", "reason": "Not relevant", "applied": True},
        )
        self.assertIn("version_id", result["artifacts"]["cv_pdf"])
        self.assertTrue(self.store.read_file(result["artifacts"]["cv_pdf"]).startswith(b"%PDF-"))
        self.assertNotIn("lock_token", result)

    def test_ready_duplicate_does_not_call_model_again(self):
        message = self.submit()
        self.service.process(message)
        self.assertEqual(self.submit(), message)
        self.assertEqual(self.service.process(message), "already ready")
        self.generate.assert_called_once()

    def test_compile_retry_reuses_model_checkpoint(self):
        message = self.submit()
        self.compiler.side_effect = RuntimeError("local compile failure")
        with self.assertRaises(RuntimeError):
            self.service.process(message)
        failed = self.store.get(**message)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["last_error"], "RuntimeError")
        self.assertIn("content", failed)
        self.compiler.side_effect = None
        self.service.process(message)
        self.generate.assert_called_once()
        self.assertEqual(self.store.get(**message)["status"], "ready")

    def test_compiler_diagnostics_download_privately_and_retry_reuses_content(self):
        message = self.submit()
        self.compiler.side_effect = DocumentCompilationError(
            timed_out=False, diagnostics=b"Private CV text\n! Undefined control sequence."
        )
        with self.assertLogs("backend.documents.service", level="ERROR") as logs:
            with self.assertRaises(DocumentCompilationError):
                self.service.process(message)
        failed = self.store.get(**message)
        self.assertIn("compilation_log", failed["failure_artifacts"])
        self.assertIn(
            b"Private CV text", self.store.read_file(failed["failure_artifacts"]["compilation_log"])
        )
        self.assertNotIn("Private CV text", str(logs.output))
        self.assertNotIn("Private CV text", str(failed))
        status = self.run_cli(
            "status", "--user-id", message["user_id"], "--generation-id", message["generation_id"]
        )
        self.assertIn("compilation_log", status["downloads"])
        self.assertIn("compilation_source", status["downloads"])
        self.compiler.side_effect = None
        self.service.process(message)
        self.generate.assert_called_once()
        self.assertEqual(self.store.get(**message)["failure_artifacts"], {})

    def test_live_lease_rejects_duplicate_and_stale_owner(self):
        message = self.submit()
        record, token = self.store.claim(**message, lease_seconds=720)
        with self.assertRaises(RuntimeError):
            self.service.process(message)
        with self.assertRaises(
            self.store.table.meta.client.exceptions.ConditionalCheckFailedException
        ):
            self.store.update_owned(record, "stale-token", {"status": "ready"})
        self.generate.assert_not_called()
        self.store.update_owned(record, token, {"status": "failed"}, release=True)
        self.assertEqual(self.service.process(message), "ready")

    def test_frozen_input_is_versioned_and_ignores_later_profile_changes(self):
        message = self.submit()
        record = self.store.get(**message)
        self.store.s3.put_object(
            Bucket=self.store.bucket, Key=record["input"]["key"], Body=b"changed"
        )
        original_name = self.request.profile.full_name
        self.request.profile.full_name = "Later profile edit"
        self.service.process(message)
        self.assertEqual(self.generate.call_args.args[0].profile.full_name, original_name)

    def test_send_failure_can_be_repaired_by_same_submission(self):
        broken = Mock()
        broken.send_message.side_effect = RuntimeError("network failure")
        with self.assertRaises(RuntimeError):
            self.store.submit(self.request, broken, self.queue)
        message = self.submit()
        self.assertEqual(self.store.table.scan()["Count"], 1)
        queued = self.sqs.receive_message(QueueUrl=self.queue)["Messages"]
        self.assertEqual(json.loads(queued[0]["Body"]), message)

    def test_force_creates_revision_and_changed_job_creates_new_request(self):
        initial = self.submit()
        self.assertNotEqual(initial, self.submit(force=True))
        self.request.job.description = "Changed responsibilities"
        self.assertNotEqual(initial, self.submit())

    def test_unsupported_cv_does_not_queue_or_write(self):
        self.request.cv_latex = "invalid"
        with self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.store.table.scan()["Count"], 0)
        self.assertNotIn("Messages", self.sqs.receive_message(QueueUrl=self.queue))

    def test_model_errors_mark_failure_without_persisting_sensitive_message(self):
        self.generate.side_effect = RuntimeError("private model text")
        message = self.submit()
        with self.assertLogs("backend.documents.service", level="ERROR") as logs:
            with self.assertRaises(RuntimeError):
                self.service.process(message)
        record = self.store.get(**message)
        self.assertEqual(record["last_error"], "RuntimeError")
        self.assertNotIn("private model text", str(record))
        self.assertNotIn("private model text", str(logs.output))

    def test_worker_rejects_model_outside_deployed_configuration(self):
        self.service.model_id = "another-model"
        with self.assertRaises(ValueError):
            self.service.process(self.submit())
        self.generate.assert_not_called()

    def test_bedrock_converse_validates_tool_response(self):
        client = self.session.client("bedrock-runtime")
        response = {
            "stopReason": "tool_use",
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tool-1",
                                "name": "deliver_documents",
                                "input": content().model_dump(mode="json"),
                            }
                        }
                    ],
                }
            },
            "usage": {"inputTokens": 50, "outputTokens": 30, "totalTokens": 80},
            "metrics": {"latencyMs": 10},
        }
        with Stubber(client) as stubber:
            stubber.add_response("converse", response)
            generated, usage = BedrockGenerator(client)(self.request)
        self.assertEqual(len(generated.edits), 3)
        self.assertEqual(usage["totalTokens"], 80)

    def test_invalid_model_edit_is_skipped_and_reported_for_review(self):
        generated = content()
        generated.edits[0].find = "Private text not in the CV"
        self.generate.return_value = generated, {}
        message = self.submit()
        with self.assertLogs("backend.documents.service", level="WARNING") as logs:
            self.assertEqual(self.service.process(message), "ready")
        record = self.store.get(**message)
        report = json.loads(self.store.read_file(record["artifacts"]["changes_json"]))
        self.assertEqual([item["applied"] for item in report], [False, True, True])
        self.assertIn("not in the CV body", report[0]["error"])
        self.assertIn("Skipped 1 of 3", str(logs.output))
        self.assertNotIn("Private text not in the CV", str(logs.output))
        self.assertNotIn("Private text not in the CV", str(record))

    def test_truncated_model_response_reports_stop_reason(self):
        client = Mock()
        client.converse.return_value = {"stopReason": "max_tokens"}
        self.service.generate = BedrockGenerator(client)
        message = self.submit()
        with self.assertRaisesRegex(DocumentError, "stop_reason=max_tokens"):
            self.service.process(message)
        self.assertIn("stop_reason=max_tokens", self.store.get(**message)["error_detail"])

    def test_bedrock_sends_cv_but_not_profile_contact_details(self):
        client = Mock()
        client.converse.return_value = {
            "stopReason": "max_tokens",
            "output": {"message": {"content": []}},
        }
        with self.assertRaises(ValueError):
            BedrockGenerator(client)(self.request)
        arguments = client.converse.call_args.kwargs
        self.assertEqual(arguments["modelId"], "us.amazon.nova-pro-v1:0")
        self.assertEqual(
            arguments["toolConfig"]["toolChoice"], {"tool": {"name": "deliver_documents"}}
        )
        self.assertEqual(
            set(arguments["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]),
            {"type", "properties", "required"},
        )
        prompt = json.loads(arguments["messages"][0]["content"][0]["text"])
        self.assertEqual(prompt["cv_latex"], CV)
        self.assertNotIn("email", prompt["candidate_facts"])
        self.assertNotIn("full_name", prompt["candidate_facts"])

    def run_cli(self, *arguments):
        outputs = {
            "DocumentGenerationsTable": self.store.table.name,
            "DocumentsBucketName": self.store.bucket,
            "DocumentQueueUrl": self.queue,
            "DocumentModelId": self.request.model_id,
            "ProfilesTable": "profiles",
            "JobsTable": "jobs",
        }
        output = io.StringIO()
        with (
            patch("backend.documents.__main__.boto3.Session", return_value=self.session),
            patch("backend.documents.__main__.stack_outputs", return_value=outputs),
            patch.object(sys, "argv", ["documents", *arguments]),
            redirect_stdout(output),
        ):
            code = cli_main()
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_cli_submission_and_status_downloads(self):
        dynamodb = self.session.resource("dynamodb")
        for name, key in [("profiles", "user_id"), ("jobs", "job_id")]:
            dynamodb.create_table(
                TableName=name,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
            )
        dynamodb.Table("profiles").put_item(Item=self.request.profile.model_dump(mode="json"))
        dynamodb.Table("jobs").put_item(
            Item={
                **self.request.job.model_dump(mode="json"),
                "source": "greenhouse",
                "last_seen": "today",
            }
        )
        submitted = self.run_cli(
            "generate",
            "--user-id",
            self.request.profile.user_id,
            "--job-id",
            "job-1",
            "--cv-file",
            str(ROOT / "backend/documents/examples/jake_cv.tex"),
        )
        message = {key: submitted[key] for key in ("user_id", "generation_id")}
        self.assertEqual(submitted["status"], "queued")
        self.service.process(message)
        result = self.run_cli(
            "status", "--user-id", message["user_id"], "--generation-id", message["generation_id"]
        )
        self.assertEqual(result["status"], "ready")
        self.assertIn("versionId=", result["downloads"]["cv_pdf"])
        self.assertEqual(result["downloads_expire_in_seconds"], 900)
        self.assertNotIn("downloads", self.store.get(**message))

    def test_expired_lease_can_be_reclaimed(self):
        message = self.submit()
        record, token = self.store.claim(**message, lease_seconds=1)
        with patch("backend.documents.storage.time.time", return_value=int(time.time()) + 3):
            newer, newer_token = self.store.claim(**message, lease_seconds=720)
            with self.assertRaises(
                self.store.table.meta.client.exceptions.ConditionalCheckFailedException
            ):
                self.store.update_owned(record, token, {"status": "ready"})
            self.store.update_owned(newer, newer_token, {"status": "failed"}, release=True)
        self.assertEqual(self.service.process(message), "ready")

    def test_worker_deadline_is_cancelled_on_failure(self):
        from backend.documents.worker import main as worker_main

        service = Mock()
        service.process.side_effect = RuntimeError("worker failure")

        def poll_once(sqs, queue_url, process, stop):
            process({"user_id": "demo-user-001", "generation_id": "test"})

        environment = {
            "GENERATIONS_TABLE": self.store.table.name,
            "DOCUMENTS_BUCKET": self.store.bucket,
            "QUEUE_URL": self.queue,
            "DOCUMENT_MODEL_ID": self.request.model_id,
        }
        with (
            patch.dict("os.environ", environment),
            patch("backend.documents.worker.boto3.Session", return_value=self.session),
            patch("backend.documents.worker.DocumentService", return_value=service),
            patch("backend.documents.worker.poll", side_effect=poll_once),
            patch("backend.documents.worker.shutdown_event"),
            patch("backend.documents.worker.threading.Timer") as timer,
            patch("backend.documents.worker.os._exit") as terminate,
        ):
            with self.assertRaises(RuntimeError):
                worker_main()
            timer.return_value.start.assert_called_once()
            timer.return_value.cancel.assert_called_once()
            self.assertEqual(timer.call_args.args[0], 600)
            timer.call_args.args[1]()
            terminate.assert_called_once_with(1)
