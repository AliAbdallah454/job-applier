"""Small helpers for normalizing provider records."""

from datetime import datetime
from html.parser import HTMLParser
from typing import Any


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    parser = _TextParser()
    parser.feed(value or "")
    return " ".join(" ".join(parser.parts).split())


def parse_datetime(value: Any) -> datetime | None:
    """Preserve timezone-aware dates without inventing timezone information."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.utcoffset() is not None else None
    except ValueError:
        return None


def extract_rows(payload: Any, key: str) -> list[dict]:
    value = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Expected a {key} array of objects")
    return value


def format_location(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        if value.get("name"):
            return str(value["name"])
        parts = [value.get(key) for key in ("city", "region", "country")]
        return ", ".join(str(part) for part in parts if part) or None
    if isinstance(value, list):
        return "; ".join(part for item in value if (part := format_location(item))) or None
    return None
