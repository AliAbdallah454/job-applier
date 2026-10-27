"""Read published jobs from one employer's public Greenhouse board."""

from html import unescape

from backend.jobs.models import JobPost, JobSource

from .base import JobFetcher
from .utils import extract_rows, html_to_text, parse_datetime


class GreenhouseJobFetcher(JobFetcher):
    source = JobSource.GREENHOUSE
    host = "boards-api.greenhouse.io"

    def _rows(self):
        payload = self._get_json(
            f"https://{self.host}/v1/boards/{self.board}/jobs",
            {"content": "true"},
            stage=f"board listings ({self.board})",
        )
        rows = extract_rows(payload, "jobs")
        total = (payload.get("meta") or {}).get("total")
        if total is not None and total != len(rows):
            raise ValueError("Incomplete board response")
        yield from rows

    def _normalize(self, row: dict) -> JobPost:
        if row["id"] is None:
            raise ValueError("Missing posting ID")
        content = row.get("content") or ""
        for _ in range(2):
            content = unescape(content)
        return JobPost(
            source=self.source,
            source_id=str(row["id"]),
            company=row.get("company_name") or self.company,
            title=row["title"],
            description=html_to_text(content),
            location=(row.get("location") or {}).get("name"),
            posting_url=row["absolute_url"],
            application_url=row["absolute_url"],
            published_at=parse_datetime(row.get("first_published")),
            published_text=row.get("first_published"),
            updated_at=parse_datetime(row.get("updated_at")),
        )
