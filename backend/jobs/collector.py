"""Collect multiple employer boards into a shared JSON-ready catalogue."""

import csv
import io
import json
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, model_validator

from backend.jobs.job_fetchers import FETCHERS, FetchError, create_fetcher


class BoardConfig(BaseModel):
    source: str
    board: str = Field(min_length=1)
    company: str | None = None
    region: Literal["global", "eu"] = "global"

    @model_validator(mode="after")
    def validate_region(self) -> "BoardConfig":
        if self.source not in FETCHERS:
            raise ValueError(f"Unsupported source: {self.source}")

        FETCHERS[self.source]._validate_board(self.board)
        if self.source != "lever" and self.region != "global":
            raise ValueError("Only Lever uses the region option")

        return self


def load_boards(path: Path, *, progress: Callable[[str], None] | None = None) -> list[BoardConfig]:
    return load_boards_text(
        path.read_text(encoding="utf-8-sig"), suffix=path.suffix, progress=progress
    )


def load_boards_text(
    text: str, *, suffix: str, progress: Callable[[str], None] | None = None
) -> list[BoardConfig]:
    if suffix.lower() == ".csv":
        return _load_csv_boards(text, progress=progress)

    rows = json.loads(text)
    if not isinstance(rows, list) or not rows:
        raise ValueError("The boards file must contain a non-empty JSON array")

    return [BoardConfig.model_validate(row) for row in rows]


def _load_csv_boards(
    text: str, *, progress: Callable[[str], None] | None = None
) -> list[BoardConfig]:
    boards = []
    skipped = Counter()

    with io.StringIO(text.lstrip("\ufeff"), newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"platform", "token"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV must have platform and token columns")

        for row in reader:
            source = (row.get("platform") or "").strip().lower()
            if source not in FETCHERS:
                skipped[source or "unknown"] += 1
                continue

            region = (row.get("region") or "").strip().lower()
            if not region:
                hosts = {
                    urlsplit(row.get(column) or "").hostname for column in ("board_url", "api_url")
                }
                region = "eu" if hosts & {"jobs.eu.lever.co", "api.eu.lever.co"} else "global"
            if region == "us":
                region = "global"

            try:
                boards.append(
                    BoardConfig(
                        source=source,
                        board=(row.get("token") or "").strip(),
                        company=(row.get("company") or "").strip() or None,
                        region=region,
                    )
                )
            except ValueError as exc:
                raise ValueError(
                    f"Invalid supported board on CSV line {reader.line_num}: {exc}"
                ) from exc
    if not boards:
        raise ValueError("CSV contains no supported job boards")
    if progress:
        selected = Counter(board.source for board in boards)
        progress(
            f"CSV: selected {len(boards)} supported rows {dict(selected)}; "
            f"skipped {sum(skipped.values())} unsupported rows {dict(skipped)}"
        )
    return boards


def collect_jobs(
    boards: list[BoardConfig],
    *,
    query: str = "",
    location: str | None = None,
    limit: int | None = None,
    total_limit: int | None = None,
    client: httpx.Client | None = None,
    request_delay: float = 1,
    progress: Callable[[str], None] | None = None,
    on_update: Callable[[dict], None] | None = None,
) -> dict:
    """Fetch each unique board once. None means all listings, not 100 per board."""
    if request_delay < 0:
        raise ValueError("request_delay must not be negative")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive, or omitted for all jobs")
    if total_limit is not None and total_limit < 1:
        raise ValueError("total_limit must be positive")

    unique_boards = {(row.source, row.region, row.board): row for row in boards}
    if not unique_boards:
        raise ValueError("At least one board is required")

    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "filters": {
            "query": query,
            "location": location,
            "limit_per_board": limit,
            "total_limit": total_limit,
        },
        "boards": [],
        "jobs": [],
    }

    def notify() -> None:
        report["updated_at"] = datetime.now(UTC).isoformat()
        report["summary"] = {
            "total_boards": len(unique_boards),
            "successful_boards": sum(row["status"] == "ok" for row in report["boards"]),
            "failed_boards": sum(row["status"] == "error" for row in report["boards"]),
            "pending_boards": len(unique_boards) - len(report["boards"]),
            "total_jobs": len(report["jobs"]),
        }

        if on_update:
            on_update(report)

    notify()

    seen_jobs = set()

    try:
        with httpx.Client() if client is None else nullcontext(client) as http:
            for index, (identity, board) in enumerate(unique_boards.items()):
                remaining = total_limit - len(report["jobs"]) if total_limit is not None else None
                if remaining == 0:
                    break
                board_limit = limit
                if remaining is not None:
                    board_limit = min(limit, remaining) if limit is not None else remaining
                if index:
                    time.sleep(request_delay)
                if progress:
                    progress(f"[{index + 1}/{len(unique_boards)}] {board.source}: {board.board}")
                result = {**board.model_dump(), "status": "ok", "job_count": 0}

                try:
                    fetcher = create_fetcher(
                        board.source,
                        board.board,
                        board.company,
                        client=http,
                        region=board.region,
                        request_delay=request_delay,
                    )

                    with fetcher:
                        jobs = fetcher.fetch(query, location=location, limit=board_limit)

                    for job in jobs:
                        key = (*identity, job.source_id or str(job.posting_url))
                        if key not in seen_jobs:
                            report["jobs"].append(
                                {
                                    **job.model_dump(mode="json"),
                                    "board": board.board,
                                    "region": board.region,
                                }
                            )
                            seen_jobs.add(key)
                            result["job_count"] += 1
                            if total_limit is not None and len(report["jobs"]) == total_limit:
                                break

                    if progress:
                        progress(f"  Collected {result['job_count']} jobs")
                except (FetchError, ValueError) as exc:
                    result.update(status="error", error=str(exc))

                    if progress:
                        progress(f"  Failed: {exc}")

                report["boards"].append(result)
                notify()
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        report["finished_at"] = datetime.now(UTC).isoformat()
        notify()
        raise

    report["status"] = (
        "limit_reached"
        if total_limit is not None and len(report["jobs"]) >= total_limit
        else "complete"
    )
    report["finished_at"] = datetime.now(UTC).isoformat()
    notify()

    return report


def save_report(report: dict, output: Path) -> None:
    """Replace the JSON atomically, keeping the previous snapshot intact on write failure."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")

        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
