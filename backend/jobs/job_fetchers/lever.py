"""Read published jobs from one employer's global or EU Lever board."""

from typing import Literal

import httpx

from backend.jobs.models import JobPost, JobSource

from .base import JobFetcher
from .utils import html_to_text


class LeverJobFetcher(JobFetcher):
    source = JobSource.LEVER

    def __init__(
        self,
        board: str,
        company: str | None = None,
        client: httpx.Client | None = None,
        *,
        region: Literal["global", "eu"] = "global",
        request_delay: float = 1,
        page_size: int = 100,
    ) -> None:
        if region not in {"global", "eu"}:
            raise ValueError("region must be global or eu")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        self.host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
        self.page_size = page_size
        super().__init__(board, company, client, request_delay=request_delay)

    def _rows(self):
        seen = set()
        offset = 0
        for _ in range(100):
            rows = self._get_json(
                f"https://{self.host}/v0/postings/{self.board}",
                {"mode": "json", "skip": offset, "limit": self.page_size},
                stage=f"board listings ({self.board}, offset {offset})",
            )
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("Expected a postings array of objects")
            ids = {str(row["id"]) for row in rows}
            if rows and not ids - seen:
                raise ValueError("Repeated pagination page")
            seen.update(ids)
            yield from rows
            if len(rows) < self.page_size:
                return
            offset += len(rows)
        raise ValueError("Lever pagination exceeded 100 pages; no results were exported")

    def _normalize(self, row: dict) -> JobPost:
        if row["id"] is None:
            raise ValueError("Missing posting ID")
        sections = [row.get("descriptionPlain") or html_to_text(row.get("description"))]
        for section in row.get("lists") or []:
            sections.extend(
                [
                    section.get("text") or "",
                    html_to_text(section.get("content")),
                ]
            )
        sections.append(row.get("additionalPlain") or html_to_text(row.get("additional")))
        return JobPost(
            source=self.source,
            source_id=str(row["id"]),
            company=self.company,
            title=row["text"],
            description="\n\n".join(part for part in sections if part),
            location=(row.get("categories") or {}).get("location"),
            posting_url=row["hostedUrl"],
            application_url=row["applyUrl"],
        )
