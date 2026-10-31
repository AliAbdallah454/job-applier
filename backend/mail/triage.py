"""Decide from sender and subject alone which mail to open, so the rest is never read."""

import re

# Platforms that send application emails for employers.
HIRING_DOMAINS = (
    "ashbyhq.com",
    "bamboohr.com",
    "breezy.hr",
    "greenhouse-mail.io",
    "greenhouse.io",
    "hire.lever.co",
    "icims.com",
    "jobvite.com",
    "lever.co",
    "myworkday.com",
    "personio.com",
    "personio.de",
    "recruitee.com",
    "rippling.com",
    "smartrecruiters.com",
    "successfactors.com",
    "taleo.net",
    "teamtailor.com",
    "welcometothejungle.com",
    "workable.com",
    "workablemail.com",
    "workday.com",
)
ALERT_SENDERS = ("jobalerts", "jobs-listings")
LINKEDIN_UPDATES = "jobs-noreply"  # Sends both job lists and application updates.
LINKEDIN_APPLICATION = re.compile(
    r"your application|you applied|votre candidature|vous avez postul", re.IGNORECASE
)
APPLICATION_SUBJECT = re.compile(
    r"\bapplication\b|\bapplying\b|\bapplied\b|\binterview|\bcandidature|\bcandidacy\b|\bpostul"
    r"|\bentretien|\bjob offer\b|\boffer letter\b|\bpromesse d.embauche\b|\boffre d.embauche\b"
    r"|interest in|int[ée]r[êe]t pour",
    re.IGNORECASE,
)


def belongs_to(domain, parent):
    return domain == parent or domain.endswith(f".{parent}")


def triage(sender, subject):
    """ "alert" for a LinkedIn job list, "application" for possible application mail, or None."""
    local, _, domain = sender.partition("@")
    # Checked first: a job list's subject can be "Applied Scientist at Acme and 5 more".
    if belongs_to(domain, "linkedin.com"):
        if local.startswith(ALERT_SENDERS):
            return "alert"
        if local.startswith(LINKEDIN_UPDATES):
            return "application" if LINKEDIN_APPLICATION.search(subject) else "alert"
        return "application" if LINKEDIN_APPLICATION.search(subject) else None
    if APPLICATION_SUBJECT.search(subject) or any(
        belongs_to(domain, parent) for parent in HIRING_DOMAINS
    ):
        return "application"
    return None
