"""Workday's public career-site CXS search and job-detail endpoints."""

import re

from backend.jobs.models import JobPost, JobSource

from .base import JobFetcher
from .utils import extract_rows, format_location, html_to_text, parse_datetime


class WorkdayJobFetcher(JobFetcher):
    source = JobSource.WORKDAY

    @staticmethod
    def _validate_board(board: str) -> str:
        if not re.fullmatch(r"wd\d+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", board):
            raise ValueError(
                "Workday token must be wdN/tenant/site, preserving site capitalization"
            )
        return board

    @property
    def host(self):
        shard, tenant, _ = self.board.split("/")
        return f"{tenant}.{shard}.myworkdayjobs.com"

    @property
    def api_base(self):
        _, tenant, site = self.board.split("/")
        return f"https://{self.host}/wday/cxs/{tenant}/{site}"

    def _rows(self):
        offset = 0
        seen = set()
        for _ in range(1000):
            payload = self._get_json(
                f"{self.api_base}/jobs",
                stage=f"listing search (offset {offset})",
                method="POST",
                body={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""},
            )
            postings = extract_rows(payload, "jobPostings")
            total = payload.get("total")
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise ValueError("Missing or invalid listing total")
            if not postings and offset < total:
                raise ValueError("Empty page before advertised listing total")
            for row in postings:
                path = row["externalPath"]
                if (
                    not isinstance(path, str)
                    or not path.startswith("/job/")
                    or any(char in path for char in ("?", "#", "\\"))
                    or ".." in path.split("/")
                ):
                    raise ValueError("Invalid Workday job path")
                if path in seen:
                    raise ValueError("Repeated Workday job across pages")
                seen.add(path)
                details = self._get_json(f"{self.api_base}{path}", stage=f"job details ({path})")
                info = details.get("jobPostingInfo") if isinstance(details, dict) else None
                if not isinstance(info, dict) or not info.get("title"):
                    raise ValueError("Missing jobPostingInfo detail object")
                yield {**row, **info, "externalPath": path}
            offset += len(postings)
            if offset >= total:
                return
        raise ValueError("Workday pagination exceeded 1000 pages")

    def _normalize(self, row: dict) -> JobPost:
        _, _, site = self.board.split("/")
        url = f"https://{self.host}/en-US/{site}{row['externalPath']}"
        published = row.get("postedOn")
        return JobPost(
            source=self.source,
            source_id=str(row.get("jobReqId") or row["externalPath"]),
            company=self.company,
            title=row["title"],
            description=html_to_text(row.get("jobDescription")),
            location=format_location(row.get("location"))
            or format_location(row.get("locationsText")),
            posting_url=url,
            application_url=f"{url}/apply",
            published_at=parse_datetime(published),
            published_text=published,
        )
