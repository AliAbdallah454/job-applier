"""Validated collection settings and S3 source loading for feeder runs."""

import logging
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.jobs.collector import load_boards_text
from backend.jobs.job_fetchers import FETCHERS

LOG = logging.getLogger(__name__)


def s3_location(uri: str) -> tuple[str, str]:
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "s3"
        or not parsed.netloc
        or not parsed.path.lstrip("/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.port
    ):
        raise ValueError("Expected an S3 object URI: s3://bucket/path/to/file")
    return parsed.netloc, parsed.path.lstrip("/")


class CollectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Object key of the company directory in the stack's job-source bucket; the bucket
    # itself comes from the stack outputs when a run is launched.
    boards_key: str = Field(min_length=1)
    region: str | None = Field(default=None, min_length=1)
    platforms: list[str] | None = Field(default=None, min_length=1)
    max_boards: int | None = Field(default=None, gt=0)
    max_jobs_per_board: int | None = Field(default=None, gt=0)

    @field_validator("platforms")
    @classmethod
    def supported_platforms(cls, platforms):
        if platforms is not None and any(platform not in FETCHERS for platform in platforms):
            raise ValueError(f"Platforms must be chosen from: {', '.join(FETCHERS)}")
        return platforms

    @field_validator("boards_key")
    @classmethod
    def supported_directory(cls, key):
        if "://" in key or key.startswith("/"):
            raise ValueError("boards_key is an object key in the job-source bucket, not a URI")
        if Path(key).suffix.lower() not in {".csv", ".json"}:
            raise ValueError("Company directory must be a CSV or JSON file")
        return key


def read_s3_text(s3, uri: str) -> str:
    bucket, key = s3_location(uri)
    response = s3.get_object(Bucket=bucket, Key=key)
    with response["Body"] as body:
        text = body.read().decode("utf-8-sig")
    LOG.info("Read %s (version=%s)", uri, response.get("VersionId", "unversioned"))
    return text


def load_collection_config(location: str) -> CollectionConfig:
    if location.startswith("s3://"):
        raise ValueError("Collection YAML must be a local file used at launch/deploy time")
    text = Path(location).read_text(encoding="utf-8-sig")
    return CollectionConfig.model_validate(yaml.safe_load(text))


def collection_environment(config: CollectionConfig, source_bucket: str) -> dict[str, str]:
    return {
        "BOARDS_S3_URI": f"s3://{source_bucket}/{config.boards_key}",
        "COLLECTION_PLATFORMS": ",".join(config.platforms or []),
        "MAX_BOARDS": str(config.max_boards) if config.max_boards is not None else "all",
        "MAX_JOBS_PER_BOARD": str(config.max_jobs_per_board)
        if config.max_jobs_per_board is not None
        else "all",
    }


def load_s3_boards(s3, uri: str):
    text = read_s3_text(s3, uri)
    return load_boards_text(text, suffix=Path(s3_location(uri)[1]).suffix)
