# Job Applier

Python backend managed with **uv**, with a separate `frontend/` directory reserved for the dashboard.
The first component fetches public employer job boards and stores normalized jobs locally.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from the repository root:

```sh
uv sync
```

## Fetch jobs

Use an actual employer board token from its Greenhouse or Lever careers URL:

```sh
uv run python -m backend.jobs fetch greenhouse BOARD_TOKEN --company "Company Name"
uv run python -m backend.jobs fetch lever BOARD_TOKEN --company "Company Name"
uv run python -m backend.jobs fetch lever BOARD_TOKEN --region eu
uv run python -m backend.jobs list --limit 20
uv run python -m backend.jobs list --source greenhouse
```

`BOARD_TOKEN` is a placeholder: for `jobs.lever.co/example`, use `example`.
These adapters fetch a selected employer's published postings, not a global job search.
They read listings only and do not submit applications.

Jobs are stored in `data/jobs.sqlite3` (ignored by Git). Override with `--db PATH` after
the subcommand. Fetching again updates the same source/region/board/posting identity,
preserving `first_seen` and refreshing `last_seen`. The returned `saved` count includes
updated records; it is not a count of new jobs. Description text, application URLs,
available metadata, and original provider payloads are retained.

Lever pagination, global/EU hosts, network timeouts, and bounded GET retries are supported.
A failed fetch saves no partial board results. Malformed records fail the fetch rather
than disappearing silently. Missing postings are not automatically marked closed, and
cross-site duplicates are not merged yet. SQLite is the local implementation, not the
planned AWS production database.

## Structure

```text
frontend/                  future dashboard
backend/jobs/
  models.py                common job format
  service.py               fetch and persist workflow
  repository.py            local SQLite storage
  adapters/                Greenhouse and Lever integrations
backend/tests/             offline adapter and persistence tests
```

## Development

```sh
uv run python -m unittest discover -s backend/tests -v
uv run ruff check .
uv run ruff format --check .
```

The initial offline suite was verified with cached runtime dependencies. Dependency
resolution could not finish in the restricted development environment because PyPI
DNS access was unavailable, so `uv.lock` has not been generated yet. Run `uv sync`
with network access to resolve dependencies and generate the lockfile.

Tests use mocked HTTP responses and never submit applications. Implementation references:
[Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) and
[Lever Postings API](https://github.com/lever/postings-api).

See [FEASIBILITY.md](FEASIBILITY.md) and [INITIAL_PLAN.md](INITIAL_PLAN.md) for the wider
product design. This implementation supersedes the plan's initial TypeScript backend proposal.
