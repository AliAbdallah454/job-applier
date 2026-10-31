"""Read-only IMAP access: headers of new mail first, and a body only when one is asked for."""

import imaplib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email import message_from_bytes, policy
from email.utils import parseaddr, parsedate_to_datetime

IMAP_TIMEOUT = 30
HEADER_BATCH = 500
MAX_MESSAGE_BYTES = 2_000_000  # Enough for the text; a large attachment is cut off.
HEADERS = "(FROM SUBJECT DATE MESSAGE-ID)"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
UID = re.compile(rb"UID (\d+)")


class MailboxError(Exception):
    """The mailbox could not be used; the message is safe to show the user."""


@dataclass(frozen=True)
class MailHeader:
    uid: int
    message_id: str
    sender: str
    subject: str
    received_at: str


def header_value(message, name):
    try:
        return str(message[name] or "").strip()
    except (ValueError, IndexError, TypeError):  # A malformed header cannot be decoded.
        return ""


def received_at(message):
    try:
        moment = parsedate_to_datetime(header_value(message, "date"))
    except (ValueError, TypeError):
        moment = datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def parse_header(uid, raw):
    message = message_from_bytes(raw, policy=policy.default)
    return MailHeader(
        uid=uid,
        message_id=header_value(message, "message-id"),
        sender=parseaddr(header_value(message, "from"))[1].lower(),
        subject=" ".join(header_value(message, "subject").split()),
        received_at=received_at(message),
    )


def message_parts(raw):
    """The plain and HTML text of a message; either can be empty."""
    message = message_from_bytes(raw, policy=policy.default)
    parts = {}
    for kind in ("plain", "html"):
        part = message.get_body(preferencelist=(kind,))
        try:
            parts[kind] = part.get_content() if part else ""
        except (LookupError, ValueError):  # An unknown or wrong charset.
            parts[kind] = ""
    return parts["plain"], parts["html"]


def fetched_messages(data):
    """(uid, raw message) pairs; a server may send the UID before or after the message."""
    for index, item in enumerate(data):
        if not isinstance(item, tuple):
            continue
        following = data[index + 1] if index + 1 < len(data) else b""
        uid = UID.search(item[0]) or UID.search(following if isinstance(following, bytes) else b"")
        if uid:
            yield int(uid[1]), item[1]


class ImapMailbox:
    """Opens INBOX read-only, so reading cannot mark mail as seen or change anything."""

    def __init__(self, host, address, password, *, connect=imaplib.IMAP4_SSL):
        self.host = host
        self.address = address
        self.password = password
        self.connect = connect
        self.imap = None
        self.validity = None
        self.next_uid = None

    def __enter__(self):
        try:
            self.imap = self.connect(self.host, timeout=IMAP_TIMEOUT)
            self.imap.login(self.address, self.password)
            status, _ = self.imap.select("INBOX", readonly=True)
        except imaplib.IMAP4.error as exc:
            raise MailboxError(
                "The mailbox refused to sign in. Check MAIL_ADDRESS and MAIL_APP_PASSWORD."
            ) from exc
        except OSError as exc:
            raise MailboxError(f"Could not reach {self.host}.") from exc
        if status != "OK":
            raise MailboxError("The mailbox could not open INBOX.")
        # The folder's UIDs only mean anything while this value stays the same.
        self.validity = self.imap.response("UIDVALIDITY")[1][0].decode()
        self.next_uid = int(self.imap.response("UIDNEXT")[1][0])
        return self

    def __exit__(self, *_):
        try:
            self.imap.logout()
        except (imaplib.IMAP4.error, OSError, AttributeError):
            pass  # Nothing is left to do with a connection that already failed.

    def _uids(self, criteria):
        status, data = self.imap.uid("SEARCH", None, criteria)
        if status != "OK":
            raise MailboxError("The mailbox could not be searched.")
        return [int(uid) for uid in data[0].split()]

    def new_headers(self, *, after_uid=None, since: date | None = None):
        """Headers, oldest first, after a UID or else from a date."""
        if after_uid is not None:
            # "n:*" always includes the newest message, even when it is older than n.
            uids = [uid for uid in self._uids(f"UID {after_uid + 1}:*") if uid > after_uid]
        else:
            uids = self._uids(f"SINCE {since.day:02d}-{MONTHS[since.month - 1]}-{since.year}")
        for start in range(0, len(uids), HEADER_BATCH):
            batch = ",".join(map(str, uids[start : start + HEADER_BATCH]))
            status, data = self.imap.uid(
                "FETCH", batch, f"(UID BODY.PEEK[HEADER.FIELDS {HEADERS}])"
            )
            if status != "OK":
                raise MailboxError("The mailbox could not return message headers.")
            found = [parse_header(uid, raw) for uid, raw in fetched_messages(data)]
            yield from sorted(found, key=lambda header: header.uid)

    def body(self, uid):
        status, data = self.imap.uid("FETCH", str(uid), f"(BODY.PEEK[]<0.{MAX_MESSAGE_BYTES}>)")
        if status != "OK":
            raise MailboxError("The mailbox could not return a message.")
        for item in data:
            if isinstance(item, tuple):
                return message_parts(item[1])
        return "", ""  # The message was deleted after its headers were read.


def configured_mailbox(values):
    """A function opening the mailbox from these settings, or None when they are missing."""
    address = values.get("MAIL_ADDRESS")
    password = (values.get("MAIL_APP_PASSWORD") or "").replace(" ", "")
    if not address or not password:
        return None
    host = values.get("MAIL_IMAP_HOST") or "imap.gmail.com"
    return lambda: ImapMailbox(host, address, password)
