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
from backend.documents.models import DocumentJob, GeneratedContent, GenerationInput, SelectedEntry
from backend.documents.render import DEFAULT_TEMPLATE, compile_pdf, escape, render_sources
from backend.documents.service import DocumentService
from backend.documents.storage import GenerationStore
from backend.profiles.models import CandidateProfile, Link

ROOT = Path(__file__).resolve().parents[2]


def request():
    profile = CandidateProfile.model_validate(
        json.loads((ROOT / "scripts/fixtures/demo_profiles.json").read_text())[0]
    )
    return GenerationInput(
        profile=profile,
        job=DocumentJob(job_id="job-1", title="Python engineer", description="Build APIs"),
        model_id="us.amazon.nova-pro-v1:0",
        cv_template=DEFAULT_TEMPLATE.read_text(),
    )


def content():
    return GeneratedContent(
        headline="Python developer",
        summary="Develops Python data tools.",
        experiences=[{"source_id": "experience-ml-001", "bullets": ["Built Python scripts."]}],
        projects=[],
        education_ids=["education-ml-001"],
        skill_names=["Python"],
        certification_ids=[],
        letter_paragraphs=["I am interested in the role.", "I built Python tools."],
        letter_source_ids=["experience-ml-001"],
    )


class RenderingTests(unittest.TestCase):
    def test_source_identity_and_escape(self):
        generated = content()
        generated.summary = r"50% & \input{secret}"
        sources = render_sources(request(), generated)
        cv = sources["cv.tex"].decode()
        self.assertIn("Example Analytics Company", cv)
        self.assertIn("2025-03", cv)
        self.assertIn(r"50\% \& \textbackslash{}input\{secret\}", cv)
        self.assertNotIn("<<SECTIONS>>", cv)
        self.assertIn("motivation_letter.tex", sources)
        self.assertEqual(escape("$_#"), r"\$\_\#")

    def test_unknown_sources_and_skills_rejected(self):
        for field, value in [
            ("education_ids", ["invented"]),
            ("skill_names", ["Invented skill"]),
            ("letter_source_ids", ["invented"]),
        ]:
            generated = content().model_copy(update={field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                generated.validate_sources(request().profile)

    def test_custom_template_is_used(self):
        custom = request().model_copy(
            update={
                "cv_template": (
                    r"\documentclass{article}\begin{document}"
                    r"Custom <<NAME>> <<SECTIONS>>\end{document}"
                )
            }
        )
        self.assertIn(b"Custom", render_sources(custom, content())["cv.tex"])

    def test_jake_template_uses_resume_macros_and_source_fields(self):
        snapshot = request()
        snapshot.cv_template = (DEFAULT_TEMPLATE.parent / "jake_resume.tex").read_text()
        snapshot.profile.full_name = "Candidate & Example"
        snapshot.profile.links = [Link(label="GitHub", url="https://github.com/example")]
        generated = content()
        generated.projects = [
            SelectedEntry(
                source_id=snapshot.profile.projects[0].id,
                bullets=[r"Built tooling with 50% fewer steps & tests."],
            )
        ]
        cv = render_sources(snapshot, generated)["cv.tex"].decode()
        self.assertIn(r"\documentclass[letterpaper,11pt]{article}", cv)
        self.assertIn(r"Candidate \& Example", cv)
        self.assertIn(r"\resumeSubheading{Python development intern}{2025-03 – 2025-08}", cv)
        self.assertIn(r"\resumeProjectHeading{\textbf{", cv)
        self.assertIn(r"\resumeItem{Built tooling with 50\% fewer steps \& tests.}", cv)
        self.assertIn(r"\href{https://github.com/example}{\underline{GitHub}}", cv)
        self.assertLess(cv.index(r"\section{Education}"), cv.index(r"\section{Experience}"))
        self.assertNotRegex(cv, r"<<[A-Z_]+>>")
        self.assertNotIn("Ali Abdallah", cv)
        self.assertNotIn(r"\input{glyphtounicode}", cv)

    def test_jake_template_omits_empty_sections(self):
        snapshot = request()
        snapshot.cv_template = (DEFAULT_TEMPLATE.parent / "jake_resume.tex").read_text()
        cv = render_sources(snapshot, content())["cv.tex"].decode()
        self.assertNotIn(r"\section{Projects}", cv)
        self.assertNotIn(r"\section{Certificates}", cv)
        self.assertIn(r"\section{Technical Skills}", cv)

    def test_nova_schema_inlines_references_and_uses_supported_root_fields(self):
        schema = tool_schema("us.amazon.nova-pro-v1:0")
        self.assertEqual(set(schema), {"type", "properties", "required"})
        self.assertNotIn('"$ref"', json.dumps(schema))
        self.assertNotIn('"$defs"', json.dumps(schema))
        self.assertEqual(schema["properties"]["experiences"]["items"]["type"], "object")
        self.assertIn("source_id", schema["properties"]["experiences"]["items"]["required"])

    def test_claude_override_keeps_existing_schema(self):
        self.assertEqual(
            tool_schema("us.anthropic.claude-sonnet-4-6"), GeneratedContent.model_json_schema()
        )

    def test_compiler_missing_is_actionable(self):
        with patch("backend.documents.render.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "XeLaTeX"):
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
        self.assertEqual(len(result["artifacts"]), 4)
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

    def test_invalid_template_does_not_queue_or_write(self):
        self.request.cv_template = "invalid"
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
        self.assertEqual(generated.skill_names, ["Python"])
        self.assertEqual(usage["totalTokens"], 80)

    def test_invalid_model_sources_have_actionable_private_safe_diagnostics(self):
        generated = content()
        generated.skill_names = ["Private invented skill"]
        self.generate.return_value = generated, {}
        message = self.submit()
        with self.assertLogs("backend.documents.service", level="ERROR") as logs:
            with self.assertRaises(DocumentError):
                self.service.process(message)
        record = self.store.get(**message)
        self.assertEqual(record["failure_stage"], "generating_content")
        self.assertIn("skill_names", record["error_detail"])
        self.assertNotIn("Private invented skill", str(record))
        self.assertIn("stage=generating_content", str(logs.output))
        self.assertNotIn("Private invented skill", str(logs.output))

    def test_truncated_model_response_reports_stop_reason(self):
        client = Mock()
        client.converse.return_value = {"stopReason": "max_tokens"}
        self.service.generate = BedrockGenerator(client)
        message = self.submit()
        with self.assertRaisesRegex(DocumentError, "stop_reason=max_tokens"):
            self.service.process(message)
        self.assertIn("stop_reason=max_tokens", self.store.get(**message)["error_detail"])

    def test_bedrock_does_not_send_contact_details(self):
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
        prompt = arguments["messages"][0]["content"][0]["text"]
        self.assertNotIn(self.request.profile.email, prompt)
        self.assertNotIn(self.request.profile.full_name, prompt)

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
            "generate", "--user-id", self.request.profile.user_id, "--job-id", "job-1"
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
