"""Job sources sharing one fetching interface."""

from .ashby import AshbyJobFetcher
from .base import FetchError, JobFetcher
from .greenhouse import GreenhouseJobFetcher
from .lever import LeverJobFetcher
from .personio import PersonioJobFetcher
from .recruitee import RecruiteeJobFetcher
from .rippling import RipplingJobFetcher
from .workable import WorkableJobFetcher
from .workday import WorkdayJobFetcher

FETCHERS = {
    "greenhouse": GreenhouseJobFetcher,
    "lever": LeverJobFetcher,
    "workday": WorkdayJobFetcher,
    "ashby": AshbyJobFetcher,
    "workable": WorkableJobFetcher,
    "recruitee": RecruiteeJobFetcher,
    "rippling": RipplingJobFetcher,
    "personio": PersonioJobFetcher,
}


def create_fetcher(
    source: str, board: str, company: str | None = None, *, region: str = "global", **kwargs
) -> JobFetcher:
    if source not in FETCHERS:
        raise ValueError(f"Unsupported job source: {source}")
    if source == "lever":
        kwargs["region"] = region
    elif region != "global":
        raise ValueError("Only Lever uses the region option")
    return FETCHERS[source](board, company, **kwargs)


__all__ = [
    "FetchError",
    "JobFetcher",
    "GreenhouseJobFetcher",
    "LeverJobFetcher",
    "WorkdayJobFetcher",
    "AshbyJobFetcher",
    "WorkableJobFetcher",
    "RecruiteeJobFetcher",
    "RipplingJobFetcher",
    "PersonioJobFetcher",
    "FETCHERS",
    "create_fetcher",
]
