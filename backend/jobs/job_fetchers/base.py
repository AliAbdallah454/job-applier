"""One shared fetching workflow and HTTP client for all job sources."""

import json
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from types import TracebackType
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from backend.jobs.models import JobPost, JobSource


class FetchError(Exception):
    """The source is unavailable, blocked, or has an unsupported page format."""


class JobFetcher(ABC):
    source: JobSource
    host: str

    def __init__(
        self,
        board: str,
        company: str | None = None,
        client: httpx.Client | None = None,
        *,
        request_delay: float = 1,
    ) -> None:
        if request_delay < 0:
            raise ValueError("request_delay must not be negative")
        self.board = self._validate_board(board)
        self.company = company or board
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client()
        self._request_delay = request_delay
        self._request_count = 0

    def __enter__(self) -> "JobFetcher":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @abstractmethod
    def _rows(self) -> Iterator[Any]:
        """Yield provider records, handling pagination within the provider."""
        ...

    @abstractmethod
    def _normalize(self, row: Any) -> JobPost:
        """Translate one provider record into the common job model."""
        ...

    def fetch(
        self, query: str = "", location: str | None = None, limit: int | None = 20
    ) -> list[JobPost]:
        """Fetch, normalize, deduplicate, and filter; None fetches all matches."""
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        jobs = []
        seen = set()
        try:
            for row in self._rows():
                job = self._normalize(row)
                identity = job.source_id or str(job.posting_url)
                if identity not in seen and self._matches(job, query, location):
                    jobs.append(job)
                seen.add(identity)
                if limit is not None and len(jobs) == limit:
                    break
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise FetchError(f"Invalid {self.source.value} board {self.board}: {exc}") from exc
        return jobs

    @staticmethod
    def _validate_board(board: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
            raise ValueError("board must be an employer board token, not a URL")
        return board

    @staticmethod
    def _matches(job: JobPost, query: str, location: str | None) -> bool:
        text = f"{job.title} {job.description}".casefold()
        return all(word in text for word in query.casefold().split()) and (
            not location or location.strip().casefold() in (job.location or "").casefold()
        )

    def _get_json(
        self,
        url: str,
        params: dict | None = None,
        *,
        stage: str,
        method: str = "GET",
        body: dict | None = None,
    ):
        text = self._get(
            url, params, stage=stage, accept="application/json", method=method, body=body
        )
        try:
            return json.loads(text)
        except ValueError as exc:
            raise FetchError(f"{self.source.value} returned invalid JSON during {stage}") from exc

    def _http_error(self, response: httpx.Response, stage: str) -> FetchError:
        lines = [
            f"{self.source.value} request failed",
            f"  Stage: {stage}",
            f"  Request: {response.request.method} {response.request.url}",
            f"  HTTP: {response.status_code} {response.reason_phrase}".rstrip(),
            f"  Requests attempted in this fetcher session: {self._request_count}",
        ]
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after is None:
                lines.append("  Retry-After: not provided; the server did not specify a wait time.")
            elif retry_after.strip().isdigit():
                lines.append(
                    f"  Retry-After: {retry_after.strip()} seconds (server-provided minimum)."
                )
            else:
                lines.append(
                    f"  Retry-After: {retry_after} (raw server value; may be an HTTP date)."
                )
            lines.extend(
                [
                    "  The response does not identify the exact rate-limit rule or blocking cause.",
                    "  No automatic retry was made. Pause requests before trying again.",
                ]
            )
        elif response.status_code in {401, 403, 999}:
            lines.append("  Access was refused. No automatic retry was made.")
        return FetchError("\n".join(lines))

    def _get(
        self,
        url: str,
        params: dict | None = None,
        *,
        stage: str = "page fetch",
        accept: str = "text/html",
        method: str = "GET",
        body: dict | None = None,
    ) -> str:
        for _ in range(4):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname not in getattr(
                self, "allowed_hosts", {self.host}
            ):
                raise FetchError(f"Unexpected destination: {url}")
            if any(
                segment in parsed.path.lower() for segment in ("/login", "/authwall", "/checkpoint")
            ):
                raise FetchError(f"{self.source.value} requires interactive access")
            if self._request_count:
                time.sleep(self._request_delay)
            self._request_count += 1
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    json=body,
                    timeout=20,
                    follow_redirects=False,
                    headers={
                        "User-Agent": "job-applier/0.1",
                        "Accept": accept,
                        **getattr(self, "request_headers", {}),
                    },
                )
                if response.is_redirect:
                    if method != "GET" and response.status_code not in {307, 308}:
                        raise FetchError(f"Unexpected redirect for {method} {url}")
                    target = response.headers.get("location")
                    if not target:
                        raise FetchError("Source returned a redirect without a destination")
                    url = urljoin(str(response.url), target)
                    params = None
                    continue
                if response.status_code in {401, 403, 429, 999}:
                    raise self._http_error(response, stage)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise self._http_error(exc.response, stage) from exc
            except httpx.HTTPError as exc:
                request_url = self._client.build_request(method, url, params=params).url
                raise FetchError(
                    f"{self.source.value} network request failed\n"
                    f"  Stage: {stage}\n"
                    f"  Request: {method} {request_url}\n"
                    f"  HTTP: no response received\n"
                    f"  Cause: {type(exc).__name__}: {exc}"
                ) from exc
            if any(
                marker in response.text.lower()
                for marker in (
                    "<title>security verification",
                    "<title>just a moment",
                    "<title>access denied",
                )
            ):
                raise FetchError(f"{self.source.value} returned an access challenge")
            return response.text
        raise FetchError("Too many redirects")
