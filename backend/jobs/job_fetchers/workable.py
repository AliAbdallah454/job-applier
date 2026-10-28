"""Workable's public widget feed, enriched with public job detail JSON."""

from urllib.parse import quote, urljoin, urlsplit

from backend.jobs.models import JobPost, JobSource

from .base import JobFetcher
from .utils import extract_rows, format_location, html_to_text, parse_datetime


class WorkableJobFetcher(JobFetcher):
    source = JobSource.WORKABLE
    host = "apply.workable.com"

    def _rows(self):
        url = f"https://{self.host}/api/v1/widget/accounts/{self.board}"
        seen_pages = set()

        for _ in range(100):
            if url in seen_pages:
                raise ValueError("Repeated widget pagination URL")

            seen_pages.add(url)
            payload = self._get_json(url, stage="widget listings")

            for row in extract_rows(payload, "jobs"):
                code = row.get("shortcode")
                if not code:
                    raise ValueError("Job is missing its shortcode")

                detail_base = f"https://{self.host}/api/v2/accounts/{self.board}/jobs"
                details = self._get_json(
                    f"{detail_base}/{quote(str(code), safe='')}",
                    stage=f"job details ({code})",
                )

                if not isinstance(details, dict) or not details.get("title"):
                    raise ValueError("Invalid Workable job detail object")

                yield {**row, **details, "shortcode": code}

            next_page = (payload.get("paging") or {}).get("next")
            if not next_page:
                return

            if not isinstance(next_page, str):
                raise ValueError("Unsupported widget pagination format")

            next_url = urljoin(url, next_page)
            parsed = urlsplit(next_url)
            if parsed.hostname != self.host or not parsed.path.startswith(
                f"/api/v1/widget/accounts/{self.board}"
            ):
                raise ValueError("Unexpected widget pagination destination")

            url = next_url

        raise ValueError("Widget pagination exceeded 100 pages")

    def _normalize(self, row: dict) -> JobPost:
        code = str(row["shortcode"])
        url = row.get("url") or f"https://{self.host}/{self.board}/j/{code}/"
        published = row.get("published_on") or row.get("published")
        return JobPost(
            source=self.source,
            source_id=code,
            company=self.company,
            title=row["title"],
            description="\n\n".join(
                part
                for key in ("description", "requirements", "benefits")
                if (part := html_to_text(row.get(key)))
            ),
            location=format_location(row.get("locations")) or format_location(row.get("location")),
            posting_url=url,
            application_url=f"https://{self.host}/{self.board}/j/{code}/apply/",
            published_at=parse_datetime(published),
            published_text=published if isinstance(published, str) else None,
        )
