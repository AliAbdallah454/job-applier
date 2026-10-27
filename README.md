# Job Applier

A job-search and application assistant for software, AI/ML, DevOps, and related
roles, including permanent jobs, internships, and alternances.

The goal is to collect opportunities once, match them to each applicant, prepare
truthful tailored documents, and track the application from submission to response.
The project is being built incrementally. **Today, it is a Python job-collection
backend with a CLI and DynamoDB storage (plus optional JSON exports).** The dashboard and application workflows
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
- DynamoDB upserts after each company; completed writes survive interrupted runs.
- Optional JSON snapshots, including per-company failure diagnostics.
- Deduplication within a collection using provider, board, region, and posting identity.
- Offline tests for provider formats, pagination, failures, limits, and exports.

There is currently no recommendation model, application submission,
mailbox connection, CV generator, running frontend, or AWS deployment. LinkedIn
and Welcome to the Jungle fetching have been removed.

## Setup

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
uv sync --locked
```

`uv` creates the local virtual environment and installs the dependencies pinned in
`uv.lock`. Runtime dependencies are HTTPX and Pydantic; Ruff handles linting and
formatting. DynamoDB storage requires AWS authentication and an existing table; see below.
JSON export mode needs no AWS credentials. No LLM key is needed.
Some employer feeds may require their own authorized access.

## DynamoDB setup

DynamoDB is the default destination. Configure AWS credentials through the standard
Boto3 credential chain (for example an IAM role or an AWS profile), and select the
region using `--aws-region` or your AWS configuration. Never commit credentials.
The runtime identity needs `dynamodb:DescribeTable` and `dynamodb:UpdateItem` on
the selected table. Network policy must allow `dynamodb.REGION.amazonaws.com`
and any authentication endpoints required by your credential provider.

Create a table once using an authorized AWS identity (this creates a billable resource):

```sh
aws dynamodb create-table --table-name job-postings --region eu-west-3 \
  --attribute-definitions AttributeName=job_id,AttributeType=S \
  --key-schema AttributeName=job_id,KeyType=HASH --billing-mode PAY_PER_REQUEST
aws dynamodb wait table-exists --table-name job-postings --region eu-west-3
```

The application never creates tables automatically. The table must have a string
partition key `job_id` and no sort key. Use `--table NAME`, `DYNAMODB_TABLE`,
or the default `job-postings`.

```sh
uv run python -m backend.jobs --boards-file config/job-board-directory.csv \
  --platforms greenhouse lever --max-boards 3 --limit 5 --total-limit 10 \
  --table job-postings --aws-region eu-west-3
```

Each item contains `job_id`, a normalized `job` map including board and region,
and UTC `first_seen`/`last_seen` timestamps. IDs are SHA-256 hashes of provider,
board, region and source ID (falling back to posting URL). Reruns update existing
items and retain `first_seen`; jobs missing from later runs are not deleted.
Batch collection writes new postings after each company. Writes are not a
transaction: on failure, earlier writes remain and the CLI returns failure.
DynamoDB's 400 KB item limit applies, including descriptions; oversized items
fail explicitly rather than silently losing content. Batch run diagnostics are
printed to the console, not stored as job items. Automatic resume is not implemented.

Use `--output PATH` to explicitly select the legacy JSON export instead of DynamoDB.
It cannot be combined with `--table`. The examples below illustrate JSON exports.

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
| `--output PATH` | Select JSON export instead of default DynamoDB storage. |
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
  --location Paris --total-limit 100 --output data/python-jobs.json
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

Add `--output data/jobs.json` to these single-company commands if you want a JSON
export instead of the default DynamoDB storage.

Replace placeholders with real employer board identifiers. A board token normally
comes from the company's hosted careers URL; Workday uses `wdN/tenant/site` and
preserves site capitalization. Lever supports global and EU hosts; CSV `US` maps
to `global` and `EU` to `eu`. `--company` supplies an employer display name.

Explicit single-company JSON exports are plain arrays written to `--output PATH`.
Batch-only controls such as `--total-limit` do not apply to this mode.

## JSON output and run behavior

When `--output` is supplied, batch JSON output is an object containing:

- `jobs`: normalized postings, with board and region provenance.
- `boards`: each attempted company's status, exported job count, and any error.
- `summary`: job count and successful, failed, and pending board counts.
- `status`: `running`, `complete`, `limit_reached`, or `interrupted`.
- Start/update/finish timestamps, input selection, and filters.

In JSON mode, the CLI saves an initial snapshot and replaces it atomically after each company
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
DynamoDB storage is covered by Moto tests without requiring an AWS account;
these tests do not establish access to a deployed AWS table.
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

1. Add catalogue queries and closed-posting reconciliation to DynamoDB storage.
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

DynamoDB tests use Moto to exercise table writes, upserts, and CLI failures offline.
Tests do not send real applications. Keep credentials, personal CVs, and mailbox
data out of Git.

- [Feasibility assessment](FEASIBILITY.md)
- [Initial implementation plan](INITIAL_PLAN.md) — earlier proposals, not a statement of shipped features.
- [Provider documentation](docs/fetchers.md)
- [Employer board directory](config/job-board-directory.csv)
