"""Ashby's documented public Job Postings API."""

import re

from backend.jobs.models import JobPost, JobSource

from .base import JobFetcher
from .utils import extract_rows, format_location, html_to_text, parse_datetime


class AshbyJobFetcher(JobFetcher):
    source = JobSource.ASHBY
    host = "api.ashbyhq.com"

    @staticmethod
    def _validate_board(board: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", board):
            raise ValueError("Invalid Ashby board token")

        return board

    def _rows(self):
        payload = self._get_json(
            f"https://{self.host}/posting-api/job-board/{self.board}", stage="board listings"
        )

        for row in extract_rows(payload, "jobs"):
            if row.get("isListed") is not False:
                yield row

    def _normalize(self, row: dict) -> JobPost:
        return JobPost(
            source=self.source,
            source_id=str(row["id"]) if row.get("id") else None,
            company=self.company,
            title=row["title"],
            description=row.get("descriptionPlain") or html_to_text(row.get("descriptionHtml")),
            location=format_location(
                [
                    row.get("location"),
                    *[item.get("location") for item in (row.get("secondaryLocations") or [])],
                ]
            ),
            posting_url=row["jobUrl"],
            application_url=row.get("applyUrl"),
            published_at=parse_datetime(row.get("publishedAt")),
            published_text=row.get("publishedAt"),
        )
