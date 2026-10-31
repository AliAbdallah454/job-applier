"""Read LinkedIn job alert emails, and one public job page when the user asks for it."""

import html
import re
from html.parser import HTMLParser

import httpx

JOB_LINK = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?\s\"'<>]*-)?(\d{6,})")
SEPARATOR = re.compile(r"^[-=_\s]{3,}$")
JOB_PAGE = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
USER_AGENT = "job-applier/0.1 (personal job search)"
BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "br", "tr", "h1", "h2", "h3", "h4"}


class JobPageError(Exception):
    """LinkedIn did not provide the job; the message is safe to show the user."""


def job_url(job_id):
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def job_from_lines(job_id, lines, *, last_lines=False):
    """Find the "Company · Place" line, as a card can also hold notes like "Actively recruiting".

    Without one the lines are title, company and place: the last ones of a text block, which
    can start with the email's introduction, or the first ones of an HTML card.
    """
    lines = [line for line in lines if line]
    marked = next((i for i, line in enumerate(lines) if i and " · " in line), None)
    if marked is not None:
        company, _, location = (part.strip() for part in lines[marked].partition(" · "))
        title = lines[marked - 1]
    else:
        title, company, location = ((lines[-3:] if last_lines else lines[:3]) + ["", "", ""])[:3]
    return {"linkedin_id": job_id, "title": title, "company": company, "location": location}


def jobs_in_text(text):
    jobs, block = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        link = JOB_LINK.search(line)
        if link:
            jobs.setdefault(link[1], job_from_lines(link[1], block, last_lines=True))
            block = []
        elif SEPARATOR.match(line):
            block = []
        elif line and not line.lower().startswith(("http", "view job", "voir l")):
            block.append(line)
    return list(jobs.values())


class AlertHtml(HTMLParser):
    """Text after each job's link, up to the next job, as alerts repeat a link per card."""

    def __init__(self):
        super().__init__()
        self.current = None
        self.blocks = {}

    def handle_starttag(self, tag, attrs):
        link = JOB_LINK.search(dict(attrs).get("href") or "") if tag == "a" else None
        if link:
            self.current = link[1]
            self.blocks.setdefault(self.current, [])
        elif tag == "a":
            self.current = None

    def handle_data(self, data):
        text = " ".join(data.split())
        if self.current and text:
            self.blocks[self.current].append(text)


def jobs_in_html(document):
    parser = AlertHtml()
    parser.feed(document)
    return [job_from_lines(job_id, lines) for job_id, lines in parser.blocks.items()]


def parse_alert(plain, document):
    """Jobs in an alert; the text part names them best, and HTML covers mail without one."""
    from_text = jobs_in_text(plain)
    from_html = {job["linkedin_id"]: job for job in jobs_in_html(document)}
    jobs = [job if job["title"] else from_html.get(job["linkedin_id"], job) for job in from_text]
    named = {job["linkedin_id"] for job in jobs}
    return jobs + [job for job_id, job in from_html.items() if job_id not in named]


class JobPage(HTMLParser):
    FIELDS = {
        "topcard__title": "title",
        "topcard__org-name-link": "company",
        "show-more-less-html__markup": "description",
    }

    def __init__(self):
        super().__init__()
        self.found = {}
        self.field = None
        self.tag = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if self.field:
            self.depth += tag == self.tag
            if self.field == "description" and tag in BLOCK_TAGS:
                self.found[self.field].append("\n")
            return
        field = next((self.FIELDS[name] for name in classes if name in self.FIELDS), None)
        if "topcard__flavor--bullet" in classes and "num-applicants__caption" not in classes:
            field = "location"
        if field and field not in self.found:
            self.field, self.tag, self.depth = field, tag, 1
            self.found[field] = []

    def handle_startendtag(self, tag, attrs):
        if self.field == "description" and tag == "br":
            self.found[self.field].append("\n")

    def handle_endtag(self, tag):
        if self.field == "description" and tag in BLOCK_TAGS:
            self.found[self.field].append("\n")
        if self.field and tag == self.tag:
            self.depth -= 1
            if self.depth == 0:
                self.field = None

    def handle_data(self, data):
        if self.field:
            self.found[self.field].append(data)

    def text(self, field):
        joined = html.unescape("".join(self.found.get(field, [])))
        lines = (" ".join(line.split()) for line in joined.splitlines())
        return "\n".join(line for line in lines if line)


def parse_job_page(document):
    parser = JobPage()
    parser.feed(document)
    return {field: parser.text(field) for field in ("title", "company", "location", "description")}


def fetch_job(job_id, *, transport=None):
    """One page, without a login. The URL is built from a number, never taken from an email."""
    if not str(job_id).isdigit():
        raise JobPageError("That is not a LinkedIn job.")
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = client.get(JOB_PAGE.format(job_id=job_id))
    except httpx.HTTPError as exc:
        raise JobPageError("LinkedIn could not be reached.") from exc
    if response.status_code == 404:
        raise JobPageError("LinkedIn no longer has this job.")
    if response.status_code != 200:
        raise JobPageError("LinkedIn would not show this job right now.")
    job = parse_job_page(response.text)
    if not job["description"]:
        raise JobPageError("LinkedIn's page did not include the job description.")
    return job
