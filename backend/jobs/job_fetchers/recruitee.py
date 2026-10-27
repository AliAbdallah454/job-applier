"""Recruitee Careers Site feed; boards requiring a token fail explicitly."""

from backend.jobs.models import JobPost, JobSource

from .base import JobFetcher
from .utils import extract_rows, format_location, html_to_text, parse_datetime


class RecruiteeJobFetcher(JobFetcher):
    source = JobSource.RECRUITEE

    def __init__(self, *args, careers_token: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_headers = {"X-Careers-Sites-Token": careers_token} if careers_token else {}

    @property
    def host(self):
        return f"{self.board}.recruitee.com"

    def _rows(self):
        yield from extract_rows(
            self._get_json(f"https://{self.host}/api/offers/", stage="board listings"), "offers"
        )

    def _normalize(self, row: dict) -> JobPost:
        url = row.get("careers_url") or row.get("url")
        if not url and row.get("slug"):
            url = f"https://{self.host}/o/{row['slug']}"
        if not url:
            raise ValueError("Offer is missing its public URL")
        return JobPost(
            source=self.source,
            source_id=str(row["id"]) if row.get("id") is not None else None,
            company=self.company,
            title=row["title"],
            description="\n\n".join(
                part
                for value in (row.get("description"), row.get("requirements"))
                if (part := html_to_text(value))
            ),
            location=format_location(row.get("locations"))
            or format_location(row.get("location"))
            or format_location(
                {
                    "city": row.get("city"),
                    "region": row.get("state_code"),
                    "country": row.get("country"),
                }
            ),
            posting_url=url,
            application_url=url,
            published_at=parse_datetime(row.get("published_at")),
            published_text=row.get("published_at"),
            updated_at=parse_datetime(row.get("updated_at")),
        )
