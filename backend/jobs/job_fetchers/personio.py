"""Personio's public XML positions feed."""

import xml.etree.ElementTree as ET

from backend.jobs.models import JobPost, JobSource

from .base import JobFetcher
from .utils import html_to_text


class PersonioJobFetcher(JobFetcher):
    source = JobSource.PERSONIO

    @property
    def host(self):
        return f"{self.board}.jobs.personio.de"

    @property
    def allowed_hosts(self):
        return {self.host, f"{self.board}.jobs.personio.com"}

    def _rows(self):
        xml = self._get(f"https://{self.host}/xml", stage="XML positions", accept="application/xml")
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise ValueError("XML entities and document types are not supported")

        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ValueError(f"Invalid XML: {exc}") from exc
        if root.tag != "workzag-jobs":
            raise ValueError("Expected a workzag-jobs XML feed")

        yield from [node for node in root if node.tag in {"position", "posting"}]

    def _normalize(self, row) -> JobPost:
        job_id = row.findtext("id")
        if not job_id:
            raise ValueError("Position is missing its ID")

        sections = []

        for section in row.findall("jobDescriptions/jobDescription"):
            for key in ("name", "value"):
                value = html_to_text(section.findtext(key))
                if value:
                    sections.append(value)
        offices = [
            row.findtext("office"),
            *[node.text for node in row.findall("additionalOffices/office")],
        ]
        url = f"https://{self.host}/job/{job_id.strip()}"
        return JobPost(
            source=self.source,
            source_id=job_id.strip(),
            company=row.findtext("subcompany") or self.company,
            title=row.findtext("name"),
            description="\n\n".join(sections),
            location="; ".join(value.strip() for value in offices if value and value.strip())
            or None,
            posting_url=url,
            application_url=f"{url}#apply",
            # createdAt describes record creation, not necessarily public publication.
        )
