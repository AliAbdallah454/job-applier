"""Generation records, immutable input snapshots and worker leases."""

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from backend.documents.latex import cv_body, diff_cv, validate_cv
from backend.documents.models import GenerationInput

DOCUMENTS = ("cv", "motivation_letter", "cover_letter")


def timestamp():
    return datetime.now(UTC).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def edited_documents(record):
    """Documents holding the user's edit; older records only show this through their history."""
    if "edited_documents" in record:
        return record["edited_documents"]
    edited = [document for document in DOCUMENTS if record.get(f"{document}_versions")]
    return list({*edited, "cv"}) if "edited_from" in record else edited


class GenerationStore:
    def __init__(self, table, s3, bucket):
        self.table = table
        self.s3 = s3
        self.bucket = bucket

    def get(self, user_id, generation_id):
        return self.table.get_item(
            Key={"user_id": user_id, "generation_id": generation_id}, ConsistentRead=True
        ).get("Item")

    def put_file(self, key, body, content_type):
        response = self.s3.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType=content_type
        )
        reference = {"bucket": self.bucket, "key": key}
        if response.get("VersionId"):
            reference["version_id"] = response["VersionId"]
        return reference

    def read_file(self, reference):
        if reference["bucket"] != self.bucket:
            raise ValueError("Generation artifact must belong to the documents bucket")
        arguments = {"Bucket": self.bucket, "Key": reference["key"]}
        if reference.get("version_id"):
            arguments["VersionId"] = reference["version_id"]
        response = self.s3.get_object(**arguments)
        with response["Body"] as stream:
            return stream.read()

    def submit(self, request: GenerationInput, sqs, queue_url, *, force=False):
        validate_cv(request.cv_latex)  # Reject an incomplete CV before anything is written.
        body = encode(request.model_dump(mode="json"))
        digest = hashlib.sha256(body).hexdigest()
        generation_id = uuid.uuid4().hex if force else digest
        user_id = request.profile.user_id
        record = self.get(user_id, generation_id)

        if record is None:
            prefix = f"users/{quote(user_id, safe='')}/generations/{generation_id}"
            # Each contender writes a different key; the winning record freezes its reference.
            snapshot = self.put_file(
                f"{prefix}/inputs/{uuid.uuid4().hex}.json", body, "application/json"
            )
            created = timestamp()
            record = {
                "user_id": user_id,
                "generation_id": generation_id,
                "job_id": request.job.job_id,
                "job_title": request.job.title,
                "job_created": f"{request.job.job_id}#{created}#{generation_id}",
                "profile_version": request.profile.version,
                "model_id": request.model_id,
                "language": request.language,
                "instructions": request.instructions,
                "prompt_version": request.prompt_version,
                "input_hash": digest,
                "input": snapshot,
                "prefix": prefix,
                "status": "queued",
                "created_at": created,
                "updated_at": created,
                "review_required": True,
            }
            try:
                self.table.put_item(
                    Item=record, ConditionExpression="attribute_not_exists(user_id)"
                )
            except self.table.meta.client.exceptions.ConditionalCheckFailedException:
                record = self.get(user_id, generation_id)

        # Sending again repairs a prior send failure; ready/active requests need no new message.
        if record["status"] in {"queued", "failed"}:
            self.enqueue(sqs, queue_url, user_id, generation_id)
        return record

    def save_edit(self, record, source, pdf, synctex=None, *, document="cv"):
        """Put the user's compiled document on the record, or None if it changed since it was read.

        The files it replaces go to <document>_versions, oldest first; the rest stays the same.
        """
        # A key of its own for each edit, so the references in the versions stay valid.
        prefix = f"{record['prefix']}/edits/{uuid.uuid4().hex}"
        files = {
            f"{document}_tex": self.put_file(
                f"{prefix}/{document}.tex", source.encode(), "application/x-tex"
            ),
            f"{document}_pdf": self.put_file(f"{prefix}/{document}.pdf", pdf, "application/pdf"),
        }
        if document == "cv":
            frozen = GenerationInput.model_validate_json(self.read_file(record["input"]))
            saved = json.loads(self.read_file(record["artifacts"]["changes_json"]))
            changes = {
                "diff": diff_cv(cv_body(frozen.cv_latex), cv_body(source)),
                # Generations from before prompt version 7 stored a list of find/replace edits.
                "explanations": saved["explanations"] if isinstance(saved, dict) else [],
            }
            files["changes_json"] = self.put_file(
                f"{prefix}/changes.json", encode(changes), "application/json"
            )
        artifacts = {**record["artifacts"], **files}
        if document == "cv":
            artifacts.pop("cv_synctex", None)  # It describes the replaced PDF.
            if synctex:
                artifacts["cv_synctex"] = self.put_file(
                    f"{prefix}/cv.synctex.gz", synctex, "application/gzip"
                )
        edited = edited_documents(record)
        replaced = {
            "saved_at": record["updated_at"],
            "by": "you" if document in edited else "model",
            "files": {name: record["artifacts"][name] for name in files},
        }
        versions = f"{document}_versions"
        try:
            response = self.table.update_item(
                Key={"user_id": record["user_id"], "generation_id": record["generation_id"]},
                UpdateExpression=(
                    "SET artifacts = :artifacts, #versions = :versions, "
                    "edited_documents = :edited, updated_at = :updated"
                ),
                # Two saves from the same read would otherwise lose one entry of the history.
                ConditionExpression="#status = :ready AND updated_at = :read",
                ExpressionAttributeNames={"#status": "status", "#versions": versions},
                ExpressionAttributeValues={
                    ":artifacts": artifacts,
                    ":versions": [*record.get(versions, []), replaced],
                    ":edited": sorted({*edited, document}),
                    ":updated": timestamp(),
                    ":ready": "ready",
                    ":read": record["updated_at"],
                },
                ReturnValues="ALL_NEW",
            )
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return None
        return response["Attributes"]

    def regenerate(self, record, request: GenerationInput, sqs, queue_url):
        """Queue a new version on this record, or None if it changed since it was read.

        The current documents stay on the record until the new ones are ready.
        """
        body = encode(request.model_dump(mode="json"))
        snapshot = self.put_file(
            f"{record['prefix']}/inputs/{uuid.uuid4().hex}.json", body, "application/json"
        )
        try:
            response = self.table.update_item(
                Key={"user_id": record["user_id"], "generation_id": record["generation_id"]},
                # Without removing it, the checkpoint would bring back the old model output.
                UpdateExpression=(
                    "SET #input = :input, input_hash = :hash, model_id = :model, "
                    "prompt_version = :prompt, instructions = :instructions, #status = :queued, "
                    "previous_saved_at = :saved, failure_artifacts = :nothing, "
                    "updated_at = :updated "
                    "REMOVE #content, lock_token, lease_until, regeneration_error"
                ),
                ConditionExpression="updated_at = :read",
                ExpressionAttributeNames={
                    "#input": "input",
                    "#status": "status",
                    "#content": "content",
                },
                ExpressionAttributeValues={
                    ":input": snapshot,
                    ":hash": hashlib.sha256(body).hexdigest(),
                    ":model": request.model_id,
                    ":prompt": request.prompt_version,
                    ":instructions": request.instructions,
                    ":queued": "queued",
                    ":saved": record["updated_at"],
                    ":nothing": {},
                    ":updated": timestamp(),
                    ":read": record["updated_at"],
                },
                ReturnValues="ALL_NEW",
            )
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return None
        saved = response["Attributes"]
        self.enqueue(sqs, queue_url, saved["user_id"], saved["generation_id"])
        return saved

    def replaced_documents(self, record):
        """Each document a finished regeneration replaces, added to that document's history."""
        edited = edited_documents(record)
        histories = {}
        for document in DOCUMENTS:
            names = [f"{document}_tex", f"{document}_pdf"]
            names += ["changes_json"] if document == "cv" else []
            if names[0] in record.get("artifacts", {}):
                replaced = {
                    "saved_at": record.get("previous_saved_at", record["updated_at"]),
                    "by": "you" if document in edited else "model",
                    "files": {name: record["artifacts"][name] for name in names},
                }
                versions = f"{document}_versions"
                histories[versions] = [*record.get(versions, []), replaced]
        return histories

    def enqueue(self, sqs, queue_url, user_id, generation_id):
        if self.get(user_id, generation_id) is None:
            raise ValueError("Generation does not exist")
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({"user_id": user_id, "generation_id": generation_id}),
        )

    def claim(self, user_id, generation_id, *, lease_seconds):
        token = uuid.uuid4().hex
        now = int(time.time())
        try:
            response = self.table.update_item(
                Key={"user_id": user_id, "generation_id": generation_id},
                UpdateExpression=(
                    "SET #status = :processing, lock_token = :token, lease_until = :until, "
                    "updated_at = :updated ADD attempts :one"
                ),
                ConditionExpression=(
                    "attribute_exists(user_id) AND #status <> :ready AND "
                    "(attribute_not_exists(lease_until) OR lease_until < :now)"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":processing": "processing",
                    ":ready": "ready",
                    ":token": token,
                    ":until": now + lease_seconds,
                    ":now": now,
                    ":updated": timestamp(),
                    ":one": 1,
                },
                ReturnValues="ALL_NEW",
            )
            return response["Attributes"], token
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            record = self.get(user_id, generation_id)
            if record and record["status"] == "ready":
                return record, None
            raise RuntimeError("Generation is missing or already being processed") from None

    def update_owned(self, record, token, values, *, release=False):
        names = {f"#v{index}": name for index, name in enumerate(values)}
        attributes = {f":v{index}": value for index, value in enumerate(values.values())}
        expression = "SET " + ", ".join(f"#v{i} = :v{i}" for i in range(len(values)))
        if release:
            expression += " REMOVE lock_token, lease_until"
        attributes.update({":token": token, ":now": int(time.time())})
        self.table.update_item(
            Key={"user_id": record["user_id"], "generation_id": record["generation_id"]},
            UpdateExpression=expression,
            ConditionExpression="lock_token = :token AND lease_until >= :now",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=attributes,
        )
