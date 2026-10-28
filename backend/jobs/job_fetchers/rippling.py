"""Rippling's public board endpoint (thin listings, often without descriptions)."""

from backend.jobs.models import JobPost, JobSource

from .base import JobFetcher
from .utils import extract_rows, format_location, html_to_text


class RipplingJobFetcher(JobFetcher):
    source = JobSource.RIPPLING
    host = "api.rippling.com"

    def _rows(self):
        payload = self._get_json(
            f"https://{self.host}/platform/api/ats/v1/board/{self.board}/jobs",
            stage="board listings",
        )

        if isinstance(payload, list):
            if any(not isinstance(row, dict) for row in payload):
                raise ValueError("Expected job objects")

            yield from payload
        else:
            yield from extract_rows(payload, "jobs")

    def _normalize(self, row: dict) -> JobPost:
        identifier = row.get("uuid") or row.get("id")
        if not identifier:
            raise ValueError("Job is missing its UUID")

        url = row.get("url") or f"https://ats.rippling.com/{self.board}/jobs/{identifier}"
        return JobPost(
            source=self.source,
            source_id=str(identifier),
            company=self.company,
            title=row.get("name") or row.get("title"),
            description=html_to_text(row.get("description")),
            location=format_location(row.get("workLocation"))
            or format_location(row.get("location")),
            posting_url=url,
            application_url=url,
        )
