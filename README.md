# Job Applier

A job-search and application assistant for software, AI/ML, DevOps, and related
roles, including permanent jobs, internships, and alternances.

The goal is to collect opportunities once, match them to each applicant, prepare
truthful tailored documents, and track the application from submission to response.
The project is being built incrementally. **Today, it is a Python job-collection
backend with a CLI and JSON exports.** The dashboard and application workflows
are planned.

## Product direction

The intended workflow is:

```text
Employer job boards → Shared job catalogue
                              ↓
                    Per-user filters and ranking
                              ↓
                    Tailored CV and motivation letter
                              ↓
                    Review / supported submission
                              ↓
                    Application history and email updates
```

Users will define their skills, experience, preferred roles, locations, contract
types, and eligibility requirements. The catalogue will be shared; recommendations,
documents, approvals, and application history will belong to each user.

Document generation will start from an existing CV, including an Overleaf/LaTeX
project, and preserve verified facts. The intended deployment is on AWS, with
Amazon Bedrock for model inference and cost-aware routing: inexpensive assessments
before more capable document generation. Provider choices and infrastructure are
still proposals, not deployed features.

## What works today

- Eight employer-board fetchers: **Greenhouse, Lever, Workday, Ashby, Workable,
  Recruitee, Rippling, and Personio**.
- A common `JobPost` model for titles, descriptions, companies, locations, URLs,
  and publication information when available.
- Batch collection from CSV or JSON board registries, or collection from one company.
- A global job cap, per-company cap, platform selection, and a company-count cap.
- Optional local keyword and location matching.
- JSON snapshots updated after each company, including failures; completed results
  survive an interrupted run.
- Deduplication within a collection using provider, board, region, and posting identity.
- Offline tests for provider formats, pagination, failures, limits, and exports.

There is currently no recommendation model, database, application submission,
mailbox connection, CV generator, running frontend, or AWS deployment. LinkedIn
and Welcome to the Jungle fetching have been removed.

## Setup

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
uv sync --locked
```

`uv` creates the local virtual environment and installs the dependencies pinned in
`uv.lock`. Runtime dependencies are HTTPX and Pydantic; Ruff handles linting and
formatting. No AWS credentials or LLM API key are needed for the current collector.
Some employer feeds may require their own authorized access.

## Collect jobs

Fetch from the supplied company-board directory, stopping at 100 jobs total:

```sh
uv run python -m backend.jobs \
  --boards-file config/job-board-directory.csv \
  --total-limit 100 \
  --output data/jobs.json
```

The current CSV contains **12,236 board rows**, not job postings: 3,576 Greenhouse,
163 Lever, 1,363 Workday, 2,486 Ashby, 1,875 Workable, 1,043 Recruitee, 864 Rippling,
and 866 Personio. A directory entry does not guarantee an accessible or active board.
Unknown providers are skipped, and supported tokens are validated before collection.

### Collection controls

| Option | Meaning |
| --- | --- |
| `--total-limit 100` | Collect at most 100 jobs across all companies combined. |
| `--limit 5` | Collect at most five matching jobs per company; CLI accepts 1–100. |
| `--max-boards 3` | Attempt only the first three selected directory rows. |
| `--platforms greenhouse lever` | Select these providers before applying the company-count cap. |
| `--output PATH` | Write the JSON snapshot here; default is `data/jobs.json`. |
| `--location Paris` | Match a location substring locally. |
| Positional query | Require every query word in the title or available description. |

Omitting job limits collects all available listings within each provider's pagination
bounds. The total cap limits collected/exported jobs; a provider's last response
may contain extra rows because APIs return whole pages. Collection follows directory
order rather than randomly sampling companies.

For a small test:

```sh
uv run python -m backend.jobs \
  --boards-file config/job-board-directory.csv \
  --platforms greenhouse lever \
  --max-boards 3 --limit 5 --total-limit 10 \
  --output data/test-jobs.json
```

To apply the existing local text filters:

```sh
uv run python -m backend.jobs "python" \
  --boards-file config/job-board-directory.csv \
  --location Paris --total-limit 100
```

These filters are keyword matching, not semantic recommendations. There is no
`--tech-only` option. Without a query, the catalogue includes non-technical roles;
a cap of 100 therefore means 100 postings, not necessarily 100 relevant tech jobs.

Use `--boards-file config/job-board-directory.csv` for batch runs with the supplied
directory. Use `--help` for the complete CLI interface.

### Fetch a single company

```sh
uv run python -m backend.jobs --source greenhouse --board BOARD_TOKEN --limit 20
uv run python -m backend.jobs --source lever --board BOARD_TOKEN --region eu
uv run python -m backend.jobs --source ashby --board BOARD_TOKEN --limit 10
uv run python -m backend.jobs --source workday --board wd1/TENANT/SITE --limit 10
```

Replace placeholders with real employer board identifiers. A board token normally
comes from the company's hosted careers URL; Workday uses `wdN/tenant/site` and
preserves site capitalization. Lever supports global and EU hosts; CSV `US` maps
to `global` and `EU` to `eu`. `--company` supplies an employer display name.

Single-company exports are plain JSON arrays. Their default filenames are
`data/SOURCE_BOARD_jobs.json`, with Workday token slashes replaced by underscores.
Batch-only controls such as `--total-limit` do not apply to this mode.

## JSON output and run behavior

Batch output is an object containing:

- `jobs`: normalized postings, with board and region provenance.
- `boards`: each attempted company's status, exported job count, and any error.
- `summary`: job count and successful, failed, and pending board counts.
- `status`: `running`, `complete`, `limit_reached`, or `interrupted`.
- Start/update/finish timestamps, input selection, and filters.

The CLI saves an initial snapshot and replaces it atomically after each company
finishes. Failed companies contribute no partial jobs; other companies continue.
Ctrl+C preserves completed company results. **A new run starts a fresh catalogue
and replaces the chosen output; automatic resume and database persistence are not
implemented.** Files under `data/` are ignored by Git.

Requests are sequential, spaced apart, and not automatically retried. Errors show
the request stage, URL, HTTP status, and `Retry-After` when supplied. Exit codes are
`0` for no board failures, `2` for partial success, `1` for all boards failing or a
setup error, and `130` for interruption. An all-failed export contains diagnostics
and an empty job list, not successfully fetched postings.

## Backend organization

```text
frontend/                       dashboard placeholder
backend/jobs/
  models.py                     JobSource, JobPost, SavedJobPost
  __main__.py                   CLI arguments and JSON export
  collector.py                  board loading, batch coordination, global limits
  job_fetchers/
    base.py                     shared HTTP handling and fetch workflow
    utils.py                    text, datetime, location, and payload helpers
    greenhouse.py               one provider module per platform
    lever.py
    ...
backend/tests/                  offline provider and collection tests
config/                         company and board directories
docs/                           provider notes and company discovery
```

All eight fetchers inherit directly from `JobFetcher`. The base class handles
normalization flow, filtering, deduplication, limits, and HTTP lifecycle. Each
provider implements `_rows()` for retrieval/pagination and `_normalize()` for
mapping one record to `JobPost`.

In batch mode, the CLI delegates to `collector.py`, which chooses fetchers,
combines results, enforces the global cap, and triggers snapshot updates.
Single-company mode calls a fetcher directly. Fetchers do not save files or submit
applications. `SavedJobPost` is reserved for future storage with observation times.

## Current limitations

Provider parsers are verified with mocked responses and synthetic JSON/XML.
Live attempts from the development environment failed DNS resolution; compatibility
with every current board has not been established. Some interfaces are documented
public feeds; others are website interfaces that can change. Recruitee may require
a careers token, and Rippling listings can omit descriptions. See
[provider formats and limitations](docs/fetchers.md).

Publication dates remain unknown when providers omit them or do not supply a
timezone. Available relative/date text is preserved without inventing an exact
instant. Cross-site duplicate matching, closed-posting reconciliation, and
persistent history remain future work.

## Next milestones

1. Add persistent storage and refresh existing postings instead of replacing a file.
2. Add tech-role classification and per-user profile/preferences filtering.
3. Evaluate inexpensive matching models before implementing personalized ranking.
4. Generate tailored CVs and motivation letters with review and version history.
5. Track applications and match email responses; add supported submission channels.
6. Build the dashboard and deploy the application, workers, and storage on AWS.

## Development and references

```sh
uv run python -m unittest discover -s backend/tests -v
uv run ruff check .
uv run ruff format --check .
```

Tests do not send real applications. Keep credentials, personal CVs, and mailbox
data out of Git.

- [Feasibility assessment](FEASIBILITY.md)
- [Initial implementation plan](INITIAL_PLAN.md) — earlier proposals, not a statement of shipped features.
- [Provider documentation](docs/fetchers.md)
- [Employer board directory](config/job-board-directory.csv)
