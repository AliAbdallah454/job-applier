"""Generate both documents, checkpoint model output, then publish artifacts."""

import json
import logging
import tempfile
from pathlib import Path

from pydantic import ValidationError

from backend.documents.errors import DocumentCompilationError, DocumentError, error_detail
from backend.documents.latex import cv_body, diff_cv
from backend.documents.models import PROMPT_VERSION, GenerationInput, TailoredCv
from backend.documents.render import compile_pdf, render_sources
from backend.documents.storage import encode, timestamp

LOG = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


class DocumentService:
    def __init__(self, store, generate, model_ids, *, timeout=1200, compile_document=compile_pdf):
        self.store = store
        self.generate = generate
        self.model_ids = model_ids
        self.timeout = timeout
        self.compile_document = compile_document

    def compile_all(self, sources, prefix, failure_artifacts):
        pdfs = {}
        with tempfile.TemporaryDirectory(prefix="job-documents-") as directory:
            for name, source in sources.items():
                path = Path(directory) / name
                path.write_bytes(source)
                try:
                    pdfs[name] = self.compile_document(path)
                except DocumentCompilationError as exc:
                    # Save before TemporaryDirectory removes the failed compilation files.
                    try:
                        failure_artifacts["compilation_source"] = self.store.put_file(
                            f"{prefix}/{name}", source, "application/x-tex"
                        )
                        failure_artifacts["compilation_log"] = self.store.put_file(
                            f"{prefix}/{path.stem}.log", exc.diagnostics, "text/plain"
                        )
                    except Exception as upload_error:
                        LOG.error(
                            "Could not save compiler diagnostics (%s)",
                            type(upload_error).__name__,
                        )
                    raise
        return pdfs

    def process(self, message):
        record, token = self.store.claim(
            message["user_id"], message["generation_id"], lease_seconds=self.timeout + 120
        )
        if token is None:
            return "already ready"

        stage = "loading_inputs"
        failure_artifacts = {}
        try:
            request = GenerationInput.model_validate_json(self.store.read_file(record["input"]))
            allowed = request.model_id in self.model_ids
            if request.profile.user_id != record["user_id"] or not allowed:
                raise DocumentError("Snapshot user or model does not match this worker")

            prefix = f"{record['prefix']}/attempts/{token}"
            content, usage, previous = None, None, None
            # A checkpoint from an older prompt has another shape, so it is generated again.
            if "content" in record and request.prompt_version == PROMPT_VERSION:
                saved = json.loads(self.store.read_file(record["content"]))
                content = TailoredCv.model_validate(saved["content"])
                usage = saved["usage"]

            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    if content is None:
                        stage = "generating_content"
                        content, usage = self.generate(request, previous)
                        reference = self.store.put_file(
                            f"{prefix}/content.json",
                            encode({"content": content.model_dump(mode="json"), "usage": usage}),
                            "application/json",
                        )
                        self.store.update_owned(record, token, {"content": reference})
                    stage = "rendering_documents"
                    sources = render_sources(request, content)
                    stage = "compiling_pdf"
                    pdfs = self.compile_all(sources, prefix, failure_artifacts)
                    break
                except (DocumentError, ValidationError) as exc:
                    if attempt == MAX_ATTEMPTS:
                        raise
                    LOG.warning(
                        "Attempt %s of %s rejected: %s", attempt, MAX_ATTEMPTS, error_detail(exc)
                    )
                    previous = {
                        "error": exc.feedback
                        if isinstance(exc, DocumentError)
                        else error_detail(exc)
                    }
                    if content:
                        previous["cv_body"] = content.cv_body
                    content = None

            stage = "uploading_documents"
            artifacts = {}
            for name, source in sources.items():
                stem = Path(name).stem
                artifacts[stem + "_tex"] = self.store.put_file(
                    f"{prefix}/{name}", source, "application/x-tex"
                )
                artifacts[stem + "_pdf"] = self.store.put_file(
                    f"{prefix}/{stem}.pdf", pdfs[name], "application/pdf"
                )
            # The diff is computed so it cannot miss a change; the explanations come from the
            # model. The rewrite is checked structurally, not fact-checked.
            changes = {
                "diff": diff_cv(cv_body(request.cv_latex), cv_body(sources["cv.tex"].decode())),
                "explanations": content.model_dump(mode="json")["changes"],
            }
            artifacts["changes_json"] = self.store.put_file(
                f"{prefix}/changes.json", encode(changes), "application/json"
            )

            self.store.update_owned(
                record,
                token,
                {
                    "status": "ready",
                    "artifacts": artifacts,
                    "usage": usage,
                    "updated_at": timestamp(),
                    "last_error": None,
                    "failure_stage": None,
                    "error_detail": None,
                    "failure_artifacts": {},
                },
                release=True,
            )
            return "ready"

        except Exception as exc:
            # Only application-authored diagnostics and validation types are safe to expose.
            detail = error_detail(exc)
            origin = exc.__traceback__
            while origin.tb_next:
                origin = origin.tb_next
            LOG.error(
                "Document generation failed: stage=%s error=%s detail=%s origin=%s:%s",
                stage,
                type(exc).__name__,
                detail,
                Path(origin.tb_frame.f_code.co_filename).name,
                origin.tb_lineno,
            )
            try:
                self.store.update_owned(
                    record,
                    token,
                    {
                        "status": "failed",
                        "last_error": (
                            exc.response.get("Error", {}).get("Code", type(exc).__name__)
                            if hasattr(exc, "response")
                            else type(exc).__name__
                        ),
                        "failure_stage": stage,
                        "error_detail": detail,
                        "failure_artifacts": failure_artifacts,
                        "updated_at": timestamp(),
                    },
                    release=True,
                )
            except self.store.table.meta.client.exceptions.ConditionalCheckFailedException:
                pass  # A newer worker owns the record; do not overwrite its state.
            raise
