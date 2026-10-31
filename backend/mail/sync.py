"""Bring the store up to date with the mailbox, one message at a time, oldest first."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.mail.classifier import visible_text
from backend.mail.linkedin import parse_alert
from backend.mail.matching import find_application, find_interest
from backend.mail.storage import apply_status, digest
from backend.mail.triage import triage

LOG = logging.getLogger(__name__)
FIRST_SYNC_DAYS = 45
UNKNOWN_COMPANY = "Unknown company"


@dataclass
class SyncResult:
    read: int = 0
    updates: int = 0
    alerts: int = 0


def message_key(header):
    identity = header.message_id or f"{header.sender}|{header.subject}|{header.received_at}"
    return digest(identity)


def attach_job(application, interest):
    return {**application, "job_id": interest["job_id"], "job_url": interest.get("url")}


def record_application(store, user_id, header, key, verdict):
    company = (verdict.company or "").strip() or UNKNOWN_COMPANY
    role = (verdict.role or "").strip() or None
    known = company != UNKNOWN_COMPANY
    # A job post you opened can name the role that a short email leaves out.
    interests = store.interests(user_id) if known else []
    interest = find_interest(interests, company, role, header.received_at)
    role = role or (interest["title"] if interest else None)
    match = find_application(store.applications(user_id), company, role) if known else None
    application = match or store.new_application(user_id, company, role)
    if role and not application.get("role"):
        application = {**application, "role": role}
    attach = interest is not None and not application.get("job_id")
    if attach:
        application = attach_job(application, interest)
    # A mention of an application with no stage still names it, as received.
    status = verdict.status or application.get("status") or "applied"
    store.save_application(
        apply_status(application, status, header.subject, header.received_at, message_key=key)
    )
    if attach:
        store.link_interest(user_id, interest["job_id"], application["application_id"])


def handle(store, user_id, mailbox, classify, header, key, kind, result):
    plain, document = mailbox.body(header.uid)
    result.read += 1
    if kind == "alert":
        jobs = parse_alert(plain, document)
        store.save_alert(user_id, key, header.subject, header.received_at, jobs)
        result.alerts += 1
        return
    verdict = classify(header.sender, header.subject, visible_text(plain, document))
    if verdict.is_application:
        record_application(store, user_id, header, key, verdict)
        result.updates += 1


def sync_mailbox(store, user_id, mailbox, classify, *, today=None):
    state = store.sync_state(user_id)
    same_folder = state.get("uid_validity") == mailbox.validity
    last_uid = int(state["last_uid"]) if same_folder and "last_uid" in state else None
    since = (today or datetime.now(UTC).date()) - timedelta(days=FIRST_SYNC_DAYS)
    result = SyncResult()

    def save_position(uid):
        store.save_sync_state(user_id, uid_validity=mailbox.validity, last_uid=uid)

    newest = last_uid
    for header in mailbox.new_headers(after_uid=last_uid, since=since):
        kind = triage(header.sender, header.subject)
        key = message_key(header)
        if kind and not store.seen(user_id, key):
            try:
                handle(store, user_id, mailbox, classify, header, key, kind, result)
            except Exception:
                # The next sync starts from this message, so it is not lost.
                if newest is not None:
                    save_position(newest)
                raise
            store.mark_seen(user_id, key, kind)
        newest = header.uid
    if newest is not None:
        save_position(newest)
    return result
