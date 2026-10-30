"""Generate both documents, checkpoint model output, then publish artifacts."""

import json
import logging
import tempfile
from pathlib import Path

from backend.documents.errors import DocumentCompilationError, DocumentError, error_detail
from backend.documents.latex import apply_edits
from backend.documents.models import GenerationInput, TailoredCv
from backend.documents.render import compile_pdf, render_sources
from backend.documents.storage import encode, timestamp

LOG = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, store, generate, model_id, *, timeout=600, compile_document=compile_pdf):
        self.store = store
        self.generate = generate
        self.model_id = model_id
        self.timeout = timeout
        self.compile_document = compile_document

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
            if request.profile.user_id != record["user_id"] or request.model_id != self.model_id:
                raise DocumentError("Snapshot user or model does not match this worker")

            prefix = f"{record['prefix']}/attempts/{token}"
            stage = "generating_content"
            if "content" in record:
                saved = json.loads(self.store.read_file(record["content"]))
                content = TailoredCv.model_validate(saved["content"])
                usage = saved["usage"]
            else:
                content, usage = self.generate(request)
                reference = self.store.put_file(
                    f"{prefix}/content.json",
                    encode({"content": content.model_dump(mode="json"), "usage": usage}),
                    "application/json",
                )
                self.store.update_owned(record, token, {"content": reference})

            stage = "rendering_documents"
            artifacts = {}
            with tempfile.TemporaryDirectory(prefix="job-documents-") as directory:
                for name, source in render_sources(request, content).items():
                    path = Path(directory) / name
                    path.write_bytes(source)
                    stage = "compiling_pdf"
                    try:
                        pdf = self.compile_document(path)
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
                    stage = "uploading_documents"
                    artifacts[path.stem + "_tex"] = self.store.put_file(
                        f"{prefix}/{name}", source, "application/x-tex"
                    )
                    artifacts[path.stem + "_pdf"] = self.store.put_file(
                        f"{prefix}/{path.stem}.pdf", pdf, "application/pdf"
                    )
            # Edits are checked structurally, not fact-checked; this is the review list,
            # including skipped edits and why they were skipped.
            _, report = apply_edits(request.cv_latex, content.edits)
            skipped = sum(not edit["applied"] for edit in report)
            if skipped:
                LOG.warning("Skipped %s of %s invalid CV edits", skipped, len(report))
            artifacts["changes_json"] = self.store.put_file(
                f"{prefix}/changes.json", encode(report), "application/json"
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
