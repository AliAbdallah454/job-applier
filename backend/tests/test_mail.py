"""Mail: triage, reading, parsing, matching and the sync, all without a real mailbox."""

import imaplib
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from unittest.mock import Mock, patch

import boto3
import httpx
from fastapi.testclient import TestClient
from moto import mock_aws

from backend.api.app import create_app
from backend.api.demo import (
    JOBS,
    DemoClassifier,
    DemoMailbox,
    create_tables,
    fake_compile,
    fake_fetch_job,
    fake_source_line,
    seed,
)
from backend.api.deps import Context, Settings
from backend.api.mail import run_sync, start_auto_sync
from backend.documents.models import ModelOption
from backend.mail.classifier import MailClassifier, MailVerdict, visible_text
from backend.mail.linkedin import (
    JobPageError,
    fetch_job,
    job_url,
    parse_alert,
    parse_job_page,
)
from backend.mail.mailbox import (
    ImapMailbox,
    MailboxError,
    MailHeader,
    configured_mailbox,
    message_parts,
    parse_header,
)
from backend.mail.matching import company_key, find_application, find_interest, same_role
from backend.mail.storage import MailStore, apply_status
from backend.mail.sync import sync_mailbox
from backend.mail.triage import triage

ALERT_TEXT = """Your job alert for engineer

Backend Engineer
Acme Corp · Paris, France
View job: https://www.linkedin.com/comm/jobs/view/4123456789/?trackingId=x%3D&refId=y

---------------------------------------------------------

Ingénieur Données
Société Générale
Lyon, France
Voir l'offre d'emploi : https://www.linkedin.com/comm/jobs/view/4123456790/?trackingId=z

---------------------------------------------------------
See all jobs: https://www.linkedin.com/comm/jobs/search/?keywords=x
"""
ALERT_HTML = """<html><body>
<a href="https://www.linkedin.com/comm/jobs/view/4123456789/?t=1"><img src="logo"></a>
<a href="https://www.linkedin.com/comm/jobs/view/4123456789/?t=2">Backend Engineer</a>
<p>Acme Corp · Paris, France</p>
<a href="https://www.linkedin.com/comm/jobs/view/software-engineer-at-beta-4123456791?t=3">
  Software Engineer</a><p>Beta · Remote</p>
<a href="https://www.linkedin.com/comm/jobs/search/">See all jobs</a><p>Footer text</p>
</body></html>"""
JOB_PAGE = """<html><h2 class="font-sans topcard__title">Senior Developer</h2>
<a class="topcard__org-name-link topcard__flavor--black-link"
   data-x="y">
   Acme &amp; Co
</a>
<span class="topcard__flavor topcard__flavor--bullet">
   Paris, France </span>
<span class="num-applicants__caption topcard__flavor--metadata topcard__flavor--bullet">
   92 applicants </span>
<div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5 relative">
  <p>Build <strong>APIs</strong>.</p><ul><li>Python</li><li>AWS</li></ul>
  <div>Nested</div>Last<br>line
</div><div class="other">Not the description</div></html>"""


def header(uid, sender="jobs@greenhouse-mail.io", subject="Thanks for applying", day=1, mid=None):
    moment = datetime(2026, 10, day, 9, tzinfo=UTC).isoformat()
    return MailHeader(uid, mid or f"<{uid}@x>", sender, subject, moment)


class FakeMailbox:
    validity = "7"

    def __init__(self, headers, bodies=None):
        self.headers = headers
        self.bodies = bodies or {}
        self.opened = []
        self.asked = None

    def new_headers(self, *, after_uid=None, since=None):
        self.asked = (after_uid, since)
        return [item for item in self.headers if item.uid > (after_uid or 0)]

    def body(self, uid):
        self.opened.append(uid)
        return self.bodies.get(uid, ("body", ""))


class TriageTests(unittest.TestCase):
    def test_sorts_mail_from_sender_and_subject(self):
        cases = [
            ("no-reply@greenhouse-mail.io", "Update", "application"),
            ("x@us.greenhouse-mail.io", "Update", "application"),
            ("rh@acme.fr", "Votre candidature chez Acme", "application"),
            ("hr@acme.com", "Interview next week", "application"),
            ("jobalerts-noreply@linkedin.com", "5 new jobs", "alert"),
            ("jobalerts-noreply@linkedin.com", "Applied Scientist at Acme and 5 more", "alert"),
            ("jobs-noreply@linkedin.com", "Ali, your application was sent to Acme", "application"),
            ("jobs-noreply@linkedin.com", "New jobs for you", "alert"),
            ("messages-noreply@linkedin.com", "You have a new message", None),
            ("deals@shop.com", "Special offer inside", None),
            ("fake@notgreenhouse.io", "Hello", None),
            ("fake@greenhouse.io.evil.com", "Hello", None),
        ]
        for sender, subject, expected in cases:
            self.assertEqual(triage(sender, subject), expected, (sender, subject))


class FakeImap:
    def __init__(self, messages=None, refuse=False):
        self.messages = messages or {}
        self.refuse = refuse
        self.commands = []
        self.readonly = None

    def __call__(self, host, timeout):
        return self

    def login(self, address, password):
        if self.refuse:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def select(self, name, readonly):
        self.readonly = readonly
        return "OK", [b"1"]

    def response(self, name):
        return name, [b"99"]

    def uid(self, command, *arguments):
        self.commands.append((command, *arguments))
        if command == "SEARCH":
            return "OK", [" ".join(map(str, self.messages)).encode()]
        uids = [int(uid) for uid in arguments[0].split(",")]
        data = []
        for uid in uids:
            data += [(f"1 (UID {uid} BODY[] {{10}}".encode(), self.messages[uid]), b")"]
        return "OK", data

    def logout(self):
        pass


def raw_email(subject="Hello", sender="HR <hr@Acme.com>", text="Plain text", html=None):
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    message["Date"] = "Fri, 02 Oct 2026 10:00:00 +0200"
    message["Message-ID"] = "<a@b>"
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    return message.as_bytes()


class MailboxTests(unittest.TestCase):
    def open(self, imap):
        return ImapMailbox("imap.test", "me@x", "secret", connect=imap)

    def test_opens_inbox_read_only_and_never_marks_mail_as_seen(self):
        imap = FakeImap({5: raw_email()})
        with self.open(imap) as mailbox:
            self.assertEqual(mailbox.validity, "99")
            list(mailbox.new_headers(since=date(2026, 3, 9)))
            mailbox.body(5)
        self.assertIs(imap.readonly, True)
        fetches = [command[2] for command in imap.commands if command[0] == "FETCH"]
        self.assertEqual(len(fetches), 2)
        self.assertTrue(all("PEEK" in fetch for fetch in fetches))
        self.assertIn(("SEARCH", None, "SINCE 09-Mar-2026"), imap.commands)

    def test_a_search_after_a_uid_drops_the_older_message_imap_always_returns(self):
        imap = FakeImap({4: raw_email(), 5: raw_email("New")})
        with self.open(imap) as mailbox:
            headers = list(mailbox.new_headers(after_uid=4))
        self.assertEqual([item.uid for item in headers], [5])
        self.assertEqual(headers[0].subject, "New")

    def test_a_uid_sent_after_the_message_still_counts(self):
        imap = FakeImap({5: raw_email("Late UID")})
        imap.uid = lambda command, *rest: (
            ("OK", [b"5"])
            if command == "SEARCH"
            else ("OK", [(b"1 (BODY[HEADER] {10}", raw_email("Late UID")), b" UID 5)"])
        )
        with self.open(imap) as mailbox:
            headers = list(mailbox.new_headers(since=date(2026, 1, 1)))
        self.assertEqual([(item.uid, item.subject) for item in headers], [(5, "Late UID")])

    def test_a_refused_login_says_what_to_check_without_the_password(self):
        with self.assertRaises(MailboxError) as caught:
            self.open(FakeImap(refuse=True)).__enter__()
        self.assertIn("MAIL_APP_PASSWORD", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_an_unreachable_server_is_a_mailbox_error(self):
        def refuse(host, timeout):
            raise OSError("no route")

        with self.assertRaisesRegex(MailboxError, "Could not reach"):
            self.open(refuse).__enter__()

    def test_headers_are_decoded_and_normalised(self):
        parsed = parse_header(1, raw_email("=?utf-8?b?Q2FuZGlkYXR1cmUgw6l0w6k=?="))
        self.assertEqual(parsed.subject, "Candidature été")
        self.assertEqual(parsed.sender, "hr@acme.com")
        self.assertEqual(parsed.received_at, "2026-10-02T08:00:00+00:00")

    def test_a_message_with_a_bad_date_still_has_a_time(self):
        raw = b"From: a@b\r\nSubject: x\r\nDate: not a date\r\n\r\n"
        self.assertRegex(parse_header(1, raw).received_at, r"^\d{4}-\d\d-\d\dT")

    def test_message_parts_read_plain_and_html(self):
        self.assertEqual(
            message_parts(raw_email(text="Plain", html="<b>Rich</b>")),
            ("Plain\n", "<b>Rich</b>\n"),
        )

    def test_a_truncated_message_can_still_be_read(self):
        plain, _ = message_parts(raw_email(text="Plain text here")[:-6])
        self.assertIn("Plain", plain)

    def test_settings_need_both_values_and_strip_the_spaces_google_shows(self):
        self.assertIsNone(configured_mailbox({"MAIL_ADDRESS": "me@x"}))
        mailbox = configured_mailbox({"MAIL_ADDRESS": "me@x", "MAIL_APP_PASSWORD": "ab cd ef"})()
        self.assertEqual((mailbox.host, mailbox.password), ("imap.gmail.com", "abcdef"))


class LinkedinTests(unittest.TestCase):
    def test_alert_text_gives_both_layouts_of_a_job(self):
        jobs = parse_alert(ALERT_TEXT, "")
        self.assertEqual(
            jobs,
            [
                {
                    "linkedin_id": "4123456789",
                    "title": "Backend Engineer",
                    "company": "Acme Corp",
                    "location": "Paris, France",
                },
                {
                    "linkedin_id": "4123456790",
                    "title": "Ingénieur Données",
                    "company": "Société Générale",
                    "location": "Lyon, France",
                },
            ],
        )

    def test_html_is_used_without_a_text_part_and_handles_repeated_links(self):
        jobs = parse_alert("", ALERT_HTML)
        self.assertEqual([job["linkedin_id"] for job in jobs], ["4123456789", "4123456791"])
        self.assertEqual(jobs[0]["title"], "Backend Engineer")
        self.assertEqual(jobs[1]["company"], "Beta")
        self.assertEqual(jobs[1]["location"], "Remote")

    def test_html_adds_jobs_the_text_part_missed(self):
        jobs = parse_alert(ALERT_TEXT, ALERT_HTML)
        self.assertEqual(len(jobs), 3)

    def test_a_note_on_a_card_is_not_taken_for_its_company(self):
        text = "Intro\n\nData Engineer\nAcme · Paris\nActively recruiting\n" + (
            "https://www.linkedin.com/comm/jobs/view/4123456789/"
        )
        job = parse_alert(text, "")[0]
        self.assertEqual(
            (job["title"], job["company"], job["location"]), ("Data Engineer", "Acme", "Paris")
        )

    def test_a_mail_with_no_jobs_gives_none(self):
        self.assertEqual(parse_alert("Nothing here", "<p>Nothing</p>"), [])

    def test_a_job_page_gives_each_field(self):
        job = parse_job_page(JOB_PAGE)
        self.assertEqual(job["title"], "Senior Developer")
        self.assertEqual(job["company"], "Acme & Co")
        self.assertEqual(job["location"], "Paris, France")
        self.assertEqual(job["description"], "Build APIs.\nPython\nAWS\nNested\nLast\nline")

    def transport(self, status, text=""):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(status, text=text)

        return httpx.MockTransport(handle), requests

    def test_fetching_builds_the_address_from_the_number_alone(self):
        transport, requests = self.transport(200, JOB_PAGE)
        self.assertEqual(fetch_job("4123456789", transport=transport)["title"], "Senior Developer")
        self.assertEqual(
            str(requests[0].url),
            "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4123456789",
        )
        self.assertNotIn("cookie", requests[0].headers)

    def test_anything_but_a_number_is_refused_without_a_request(self):
        transport, requests = self.transport(200, JOB_PAGE)
        for bad in ("../../evil", "http://evil.test", "12a"):
            with self.assertRaises(JobPageError):
                fetch_job(bad, transport=transport)
        self.assertEqual(requests, [])

    def test_a_refusal_or_a_page_without_a_description_is_explained(self):
        for status, text, words in (
            (404, "", "no longer"),
            (429, "", "would not"),
            (999, "", "would not"),
            (302, "", "would not"),  # A redirect to the login page is not followed.
            (200, "<html>Sign in</html>", "description"),
        ):
            transport, _ = self.transport(status, text)
            with self.assertRaisesRegex(JobPageError, words):
                fetch_job("4123456789", transport=transport)

    def test_a_network_failure_is_explained(self):
        def fail(request):
            raise httpx.ConnectError("down")

        with self.assertRaisesRegex(JobPageError, "could not be reached"):
            fetch_job("4123456789", transport=httpx.MockTransport(fail))


class MatchingTests(unittest.TestCase):
    def test_company_names_compare_without_legal_words_case_or_accents(self):
        self.assertEqual(company_key("ACME, Inc."), company_key("acme"))
        self.assertEqual(company_key("Société Générale SA"), company_key("societe generale"))
        self.assertEqual(company_key("Group"), "group")

    def test_roles_match_on_most_of_the_shorter_title(self):
        self.assertTrue(same_role("Software Engineer, Early Career (NYC)", "Software Engineer"))
        self.assertTrue(same_role(None, "Anything"))
        self.assertFalse(same_role("Backend Engineer", "Frontend Engineer"))

    def test_the_most_recent_matching_application_wins(self):
        old = {"company": "Acme", "role": "Engineer", "updated_at": "2026-01-01"}
        new = {"company": "ACME Inc", "role": "Engineer II", "updated_at": "2026-02-01"}
        other = {"company": "Beta", "role": "Engineer", "updated_at": "2026-03-01"}
        self.assertIs(find_application([old, new, other], "acme", "Engineer"), new)
        self.assertIsNone(find_application([old], "", "Engineer"))

    def test_an_opened_job_matches_only_a_later_email_about_it(self):
        opened = {
            "title": "Backend Engineer",
            "company": "Acme Inc",
            "opened_at": "2026-10-02T09:00:00+00:00",
        }
        later = "2026-10-03T09:00:00+00:00"
        self.assertIs(find_interest([opened], "ACME", "Backend Engineer", later), opened)
        self.assertIs(find_interest([opened], "Acme", None, later), opened)
        self.assertIsNone(find_interest([opened], "Beta", "Backend Engineer", later))
        self.assertIsNone(find_interest([opened], "Acme", "Designer", later))
        self.assertIsNone(find_interest([opened], "", "Backend Engineer", later))
        earlier = "2026-10-01T09:00:00+00:00"
        self.assertIsNone(find_interest([opened], "Acme", "Backend Engineer", earlier))
        linked = {**opened, "application_id": "a1"}
        self.assertIsNone(find_interest([linked], "Acme", "Backend Engineer", later))

    def test_the_most_recently_opened_job_wins_when_an_email_names_no_role(self):
        old = {"title": "A", "company": "Acme", "opened_at": "2026-01-01"}
        new = {"title": "B", "company": "Acme", "opened_at": "2026-02-01"}
        self.assertIs(find_interest([old, new], "Acme", None, "2026-03-01"), new)

    def test_the_newest_event_sets_the_status_even_when_read_out_of_order(self):
        application = {"events": []}
        application = apply_status(application, "rejected", "No", "2026-02-01", message_key="b")
        application = apply_status(application, "applied", "Yes", "2026-01-01", message_key="a")
        self.assertEqual(application["status"], "rejected")
        self.assertEqual(len(application["events"]), 2)
        self.assertEqual(
            apply_status(application, "offer", "No", "2026-03-01", message_key="a"), application
        )


class ClassifierTests(unittest.TestCase):
    def client(self, content):
        client = Mock()
        client.converse.return_value = {"output": {"message": {"content": content}}}
        return client

    def test_sends_a_short_body_and_validates_the_answer(self):
        verdict = {"is_application": True, "company": "Acme", "role": None, "status": "interview"}
        client = self.client([{"toolUse": {"name": "record_email", "input": verdict}}])
        result = MailClassifier(client)("a@b", "Subject", "x" * 50000)
        self.assertEqual(result.status, "interview")
        arguments = client.converse.call_args.kwargs
        self.assertLess(len(arguments["messages"][0]["content"][0]["text"]), 7000)
        self.assertIn("untrusted", arguments["system"][0]["text"])

    def test_an_unknown_status_or_missing_answer_is_refused(self):
        bad = {"is_application": True, "status": "hired"}
        with self.assertRaises(ValueError):
            MailClassifier(self.client([{"toolUse": {"input": bad}}]))("a", "b", "c")
        with self.assertRaises(ValueError):
            MailClassifier(self.client([{"text": "no"}]))("a", "b", "c")

    def test_visible_text_prefers_plain_and_drops_html_markup(self):
        self.assertEqual(visible_text("Plain", "<b>Rich</b>"), "Plain")
        self.assertEqual(
            visible_text("", "<style>a{}</style><p>Hi <b>there</b></p><script>x</script>"),
            "Hi there",
        )


@mock_aws
class SyncTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"AWS_DEFAULT_REGION": "us-east-1"})
        environment.start()
        self.addCleanup(environment.stop)
        db = boto3.resource("dynamodb", region_name="us-east-1")
        create_tables(db)
        self.store = MailStore(db.Table("mail"))
        self.verdict = MailVerdict(
            is_application=True, company="Acme", role="Engineer", status="applied"
        )
        self.classify = Mock(return_value=self.verdict)

    def sync(self, mailbox, **options):
        return sync_mailbox(self.store, "ali", mailbox, self.classify, **options)

    def test_only_likely_mail_is_opened_and_bodies_are_not_stored(self):
        mailbox = FakeMailbox(
            [
                header(1, sender="deals@shop.com", subject="Sale"),
                header(2),
            ],
            {2: ("A very private body", "")},
        )
        self.sync(mailbox)
        self.assertEqual(mailbox.opened, [2])
        items = self.store.table.scan()["Items"]
        self.assertNotIn("A very private body", str(items))
        self.assertEqual(len(self.store.applications("ali")), 1)

    def test_the_first_sync_looks_back_and_the_next_continues_after_the_last_uid(self):
        mailbox = FakeMailbox([header(1), header(2)])
        self.sync(mailbox, today=date(2026, 10, 31))
        self.assertEqual(mailbox.asked, (None, date(2026, 9, 16)))
        self.sync(mailbox)
        self.assertEqual(mailbox.asked[0], 2)

    def test_a_new_folder_validity_starts_again_without_duplicating_events(self):
        mailbox = FakeMailbox([header(1)])
        self.sync(mailbox)
        mailbox.validity = "8"
        self.sync(mailbox)
        self.assertIsNone(mailbox.asked[0])
        self.assertEqual(self.classify.call_count, 1)
        self.assertEqual(len(self.store.applications("ali")[0]["events"]), 1)

    def test_emails_about_one_application_build_its_history(self):
        self.classify.side_effect = [
            self.verdict,
            self.verdict.model_copy(update={"status": "interview", "role": None}),
            self.verdict.model_copy(update={"company": "Beta Inc"}),
        ]
        self.sync(FakeMailbox([header(1, day=1), header(2, day=2), header(3, day=3)]))
        applications = {item["company"]: item for item in self.store.applications("ali")}
        self.assertEqual(set(applications), {"Acme", "Beta Inc"})
        self.assertEqual(applications["Acme"]["status"], "interview")
        self.assertEqual(len(applications["Acme"]["events"]), 2)

    def test_mail_that_is_not_an_application_changes_nothing(self):
        self.classify.return_value = MailVerdict(is_application=False)
        self.sync(FakeMailbox([header(1)]))
        self.assertEqual(self.store.applications("ali"), [])

    def test_an_email_with_no_company_stays_on_its_own(self):
        self.classify.return_value = MailVerdict(is_application=True, status="applied")
        self.sync(FakeMailbox([header(1), header(2)]))
        companies = [item["company"] for item in self.store.applications("ali")]
        self.assertEqual(companies, ["Unknown company", "Unknown company"])

    def open_job(self, job_id="job-1", title="Engineer", company="Acme", day=1):
        job = {"job_id": job_id, "title": title, "company": company, "url": "https://job.test"}
        self.store.save_interest("ali", job, datetime(2026, 10, day, 8, tzinfo=UTC).isoformat())

    def test_an_email_links_an_application_to_a_job_opened_without_tailoring(self):
        self.open_job()
        self.sync(FakeMailbox([header(1)]))
        application = self.store.applications("ali")[0]
        self.assertEqual(application["job_id"], "job-1")
        self.assertEqual(application["job_url"], "https://job.test")
        self.assertEqual(
            self.store.interest("ali", "job-1")["application_id"], application["application_id"]
        )

    def test_an_opened_job_names_the_role_that_an_email_leaves_out(self):
        self.open_job(title="Data Engineer")
        self.classify.return_value = MailVerdict(
            is_application=True, company="Acme", status="applied"
        )
        self.sync(FakeMailbox([header(1)]))
        self.assertEqual(self.store.applications("ali")[0]["role"], "Data Engineer")

    def test_opening_a_job_alone_never_creates_an_application(self):
        self.open_job()
        self.sync(FakeMailbox([header(1, sender="deals@shop.com", subject="Sale")]))
        self.assertEqual(self.store.applications("ali"), [])

    def test_a_later_email_keeps_the_job_and_leaves_other_opened_jobs_in_the_pool(self):
        self.open_job()
        self.open_job("job-2", title="Designer", day=2)
        self.classify.side_effect = [
            self.verdict,
            self.verdict.model_copy(update={"status": "interview"}),
        ]
        self.sync(FakeMailbox([header(1, day=3), header(2, day=4)]))
        application = self.store.applications("ali")[0]
        self.assertEqual(application["job_id"], "job-1")
        self.assertEqual(len(application["events"]), 2)
        self.assertNotIn("application_id", self.store.interest("ali", "job-2"))

    def test_opening_a_matched_job_again_keeps_its_link(self):
        self.open_job()
        self.sync(FakeMailbox([header(1, day=2)]))
        self.open_job(day=3)
        self.assertIn("application_id", self.store.interest("ali", "job-1"))

    def test_a_failed_email_is_retried_next_time_and_nothing_is_lost(self):
        self.classify.side_effect = [self.verdict, RuntimeError("throttled")]
        mailbox = FakeMailbox([header(1), header(2), header(3)])
        with self.assertRaises(RuntimeError):
            self.sync(mailbox)
        self.assertEqual(self.store.sync_state("ali")["last_uid"], 1)

        self.classify.side_effect = None
        self.sync(mailbox)
        self.assertEqual(mailbox.asked[0], 1)
        self.assertEqual(len(self.store.applications("ali")[0]["events"]), 3)

    def test_an_alert_becomes_a_group_of_jobs_without_the_model(self):
        mailbox = FakeMailbox(
            [header(1, sender="jobalerts-noreply@linkedin.com", subject="2 new jobs")],
            {1: (ALERT_TEXT, "")},
        )
        self.sync(mailbox)
        self.classify.assert_not_called()
        alerts = self.store.alerts("ali")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(alerts[0]["jobs"]), 2)
        self.assertEqual(set(self.store.jobs("ali")), {"4123456789", "4123456790"})

    def test_the_same_job_in_a_later_alert_keeps_its_description(self):
        alert = {"sender": "jobalerts-noreply@linkedin.com", "subject": "New"}
        mailbox = FakeMailbox(
            [header(1, **alert), header(2, **alert)], {1: (ALERT_TEXT, ""), 2: (ALERT_TEXT, "")}
        )
        self.sync(mailbox)
        self.store.save_job_details("ali", "4123456789", {"description": "Saved"})
        mailbox.headers.append(header(3, **alert))
        mailbox.bodies[3] = (ALERT_TEXT, "")
        self.sync(mailbox)
        self.assertEqual(self.store.job("ali", "4123456789")["description"], "Saved")


CV = rb"""\documentclass{article}
\begin{document}
\section{Experience}
\begin{itemize}\item Built things\end{itemize}
\end{document}
"""


@mock_aws
class MailApiTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"AWS_DEFAULT_REGION": "us-east-1"})
        environment.start()
        self.addCleanup(environment.stop)
        session = boto3.Session(region_name="us-east-1")
        queue = seed(session, "ali")
        self.settings = dict(
            user_id="ali",
            jobs_table="jobs",
            classifications_table="classifications",
            profiles_table="profiles",
            generations_table="generations",
            documents_bucket="documents",
            document_queue_url=queue,
            document_models=(ModelOption(id="model", label="Model"),),
            compile_document=fake_compile,
            find_source_line=fake_source_line,
            mail_table="mail",
            open_mailbox=DemoMailbox,
            classify_mail=DemoClassifier,
            fetch_job=fake_fetch_job,
        )
        self.client = self.start(self.settings)

    def start(self, settings):
        self.context = Context(Settings(**settings), region="us-east-1")
        return TestClient(create_app(self.context), base_url="http://localhost")

    def synced(self):
        run_sync(self.context, "ali")

    def test_status_says_what_is_missing(self):
        self.assertIsNone(self.client.get("/api/mail/status").json()["problem"])
        client = self.start({**self.settings, "open_mailbox": None})
        self.assertEqual(client.get("/api/mail/status").json()["problem"], "credentials")
        self.assertEqual(client.post("/api/mail/sync").status_code, 409)
        client = self.start({**self.settings, "mail_table": None})
        self.assertEqual(client.get("/api/mail/status").json()["problem"], "table")
        self.assertEqual(client.get("/api/applications").json(), [])
        self.assertEqual(client.get("/api/mail/alerts").json(), [])
        self.assertEqual(client.post("/api/mail/sync").status_code, 503)

    def test_nothing_starts_automatically_until_the_mailbox_can_be_used(self):
        self.start({**self.settings, "open_mailbox": None})
        with patch("backend.api.mail.threading.Thread") as thread:
            start_auto_sync(self.context, "ali")
        thread.assert_not_called()

    def test_a_sync_reports_when_it_finished_and_a_login_problem(self):
        self.synced()
        state = self.client.get("/api/mail/status").json()
        self.assertIsNotNone(state["last_synced_at"])
        self.assertIsNone(state["last_error"])

        def refuse():
            raise MailboxError("The mailbox refused to sign in.")

        self.start({**self.settings, "open_mailbox": Mock(side_effect=refuse)})
        self.synced()
        state = TestClient(create_app(self.context), base_url="http://localhost")
        self.assertIn("refused", state.get("/api/mail/status").json()["last_error"])

    def test_a_second_sync_during_the_first_does_nothing(self):
        opened = Mock()
        self.start({**self.settings, "open_mailbox": opened})
        with self.context.mail_sync_lock:
            self.synced()
        opened.assert_not_called()

    def test_applications_show_their_status_and_history_newest_first(self):
        self.synced()
        applications = self.client.get("/api/applications").json()
        self.assertEqual(
            [(item["company"], item["status"]) for item in applications],
            [("Northwind", "rejected"), ("Example Labs", "interview")],
        )
        self.assertEqual([item["status"] for item in applications[1]["events"]], [
            "applied", "interview",
        ])  # fmt: skip

    def test_you_can_correct_a_status_and_remove_an_entry_for_good(self):
        self.synced()
        first = self.client.get("/api/applications").json()[0]
        url = f"/api/applications/{first['application_id']}"
        changed = self.client.patch(url, json={"status": "offer"}).json()
        self.assertEqual(changed["status"], "offer")
        self.assertEqual(changed["events"][-1]["subject"], "Changed by you")
        self.assertEqual(self.client.patch(url, json={"status": "hired"}).status_code, 422)

        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.patch(url, json={"status": "offer"}).status_code, 404)
        self.context.mail.save_sync_state("ali", uid_validity="other")
        self.synced()
        companies = [item["company"] for item in self.client.get("/api/applications").json()]
        self.assertEqual(companies, ["Example Labs"])

    def test_alerts_list_their_jobs(self):
        self.synced()
        alerts = self.client.get("/api/mail/alerts").json()
        self.assertEqual(len(alerts), 1)
        job = alerts[0]["jobs"][0]
        self.assertEqual(job["job_id"], "linkedin-1000000001")
        self.assertEqual(job["company"], "Example Labs")
        self.assertEqual(job["url"], job_url("1000000001"))
        self.assertFalse(job["has_description"])

    def test_a_job_is_fetched_once_and_a_blocked_one_can_be_pasted(self):
        self.synced()
        url = "/api/mail/jobs/1000000001/description"
        fetch = Mock(return_value={"description": "Fetched", "title": "", "company": "Real Co"})
        self.start({**self.settings, "fetch_job": fetch})
        client = TestClient(create_app(self.context), base_url="http://localhost")
        body = client.post(url, json={}).json()
        self.assertTrue(body["has_description"])
        self.assertEqual(body["company"], "Real Co")
        self.assertEqual(body["title"], "Junior Backend Engineer")  # Blank fields do not erase.
        client.post(url, json={})
        fetch.assert_called_once_with("1000000001")

        blocked = "/api/mail/jobs/1000000003/description"
        client = self.start(self.settings)
        response = client.post(blocked, json={})
        self.assertEqual(response.status_code, 502)
        self.assertIn("would not show", response.json()["detail"])
        self.assertTrue(
            client.post(blocked, json={"description": "Pasted"}).json()["has_description"]
        )

    def test_only_jobs_from_your_alerts_can_be_fetched(self):
        self.synced()
        fetch = Mock()
        client = self.start({**self.settings, "fetch_job": fetch})
        self.assertEqual(
            client.post("/api/mail/jobs/9999999/description", json={}).status_code, 404
        )
        self.assertEqual(
            client.post("/api/mail/jobs/not-a-job/description", json={}).status_code, 422
        )
        fetch.assert_not_called()

    def open_alert_job(self):
        return self.client.post(
            "/api/applications/interests", json={"job_id": "linkedin-1000000002"}
        )

    def age_interest(self, job_id, days):
        moment = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        self.context.mail.table.update_item(
            Key={"user_id": "ali", "item_id": f"interest#{job_id}"},
            UpdateExpression="SET opened_at = :moment",
            ExpressionAttributeValues={":moment": moment},
        )

    def test_opening_a_job_from_either_list_records_it_from_the_server(self):
        response = self.client.post("/api/applications/interests", json={"job_id": "demo-2"})
        self.assertEqual(response.status_code, 204)
        saved = self.context.mail.interest("ali", "demo-2")
        self.assertEqual(saved["title"], JOBS[2][0])
        self.assertEqual(saved["company"], JOBS[2][1])

        self.synced()
        self.assertEqual(self.open_alert_job().status_code, 204)
        saved = self.context.mail.interest("ali", "linkedin-1000000002")
        self.assertEqual(saved["title"], "Graduate Data Engineer")
        self.assertEqual(saved["url"], job_url("1000000002"))

    def test_an_unknown_job_is_refused_and_a_missing_table_is_ignored(self):
        response = self.client.post("/api/applications/interests", json={"job_id": "nothing"})
        self.assertEqual(response.status_code, 404)
        client = self.start({**self.settings, "mail_table": None})
        response = client.post("/api/applications/interests", json={"job_id": "nothing"})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(client.get("/api/applications/interests").json(), [])

    def test_a_job_opened_without_tailoring_is_caught_by_its_email(self):
        self.synced()
        applications = {
            item["company"]: item for item in self.client.get("/api/applications").json()
        }
        self.assertEqual(applications["Example Labs"]["job_id"], "demo-0")
        self.assertEqual(applications["Example Labs"]["job_url"], "https://example.com")
        self.assertIsNone(applications["Example Labs"]["generation_id"])
        self.assertIsNone(applications["Northwind"]["job_id"])

    def test_it_asks_about_opened_jobs_only_while_an_email_is_overdue(self):
        self.synced()
        url = "/api/applications/interests"
        self.assertEqual([item["job_id"] for item in self.client.get(url).json()], ["demo-1"])
        self.age_interest("demo-1", 0)
        self.assertEqual(self.client.get(url).json(), [])
        self.age_interest("demo-1", 31)
        self.assertEqual(self.client.get(url).json(), [])

    def test_saying_you_applied_makes_an_application_and_ends_the_question(self):
        self.synced()
        response = self.client.post("/api/applications/interests/demo-1/applied")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["company"], body["status"]), (JOBS[1][1], "applied"))
        self.assertEqual(body["job_id"], "demo-1")
        self.assertEqual(body["events"][0]["subject"], "Marked by you")
        self.assertEqual(self.client.get("/api/applications/interests").json(), [])

    def test_saying_you_applied_uses_the_application_an_email_already_started(self):
        self.synced()
        self.context.mail.save_interest(
            "ali",
            {"job_id": "x", "title": "Junior Backend Engineer", "company": "Example Labs"},
            "2026-01-01T00:00:00+00:00",
        )
        self.client.post("/api/applications/interests/x/applied")
        companies = [item["company"] for item in self.client.get("/api/applications").json()]
        self.assertEqual(companies.count("Example Labs"), 1)

    def test_saying_you_did_not_apply_removes_the_job_from_the_pool(self):
        self.synced()
        url = "/api/applications/interests/demo-1"
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get("/api/applications/interests").json(), [])
        self.assertEqual(self.client.post(f"{url}/applied").status_code, 404)

    def tailor(self, job_id):
        cv = self.client.get("/api/cvs").json()[0]["cv_id"]
        return self.client.post("/api/generations", json={"job_id": job_id, "cv_id": cv})

    def test_a_job_from_an_email_needs_its_description_then_can_be_tailored(self):
        self.synced()
        self.assertEqual(self.tailor("linkedin-1000000001").status_code, 409)
        self.client.post("/api/mail/jobs/1000000001/description", json={})
        response = self.tailor("linkedin-1000000001")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["job_title"], "Junior Backend Engineer")
        self.assertEqual(body["company"], "Example Labs")
        self.assertEqual(body["job_url"], job_url("1000000001"))
        self.assertEqual(self.tailor("linkedin-404").status_code, 404)

    def test_an_application_links_to_the_tailored_cv_for_that_job(self):
        self.synced()
        self.client.post("/api/mail/jobs/1000000001/description", json={})
        generation = self.tailor("linkedin-1000000001").json()["generation_id"]
        self.context.generations.table.update_item(
            Key={"user_id": "ali", "generation_id": generation},
            UpdateExpression="SET #status = :ready",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":ready": "ready"},
        )
        applications = {
            item["company"]: item for item in self.client.get("/api/applications").json()
        }
        self.assertEqual(applications["Example Labs"]["generation_id"], generation)
        self.assertIsNone(applications["Northwind"]["generation_id"])
