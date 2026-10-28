# Job Applier

A job-search and application assistant for software, AI/ML, DevOps, and related
roles, including permanent jobs, internships, and alternances.

The goal is to collect opportunities once, match them to each applicant, prepare
truthful tailored documents, and track the application from submission to response.
The project is being built incrementally. **Today, it is a Python job-collection
backend with CLI tools, two-table DynamoDB storage, and a CDK deployment for parallel
Docker/Fargate workers.** The dashboard and application workflows are planned.

For deployment, API-key setup and a small first run, see the
[AWS pipeline guide](infra/README.md).

The feeder, scraper and classifier have separate Dockerfiles and ECS images.
Build them from the repository root:

```sh
docker build --platform linux/amd64 -f containers/Dockerfile.seed -t job-applier-seed:test .
docker build --platform linux/amd64 -f containers/Dockerfile.scrape -t job-applier-scrape:test .
docker build --platform linux/amd64 -f containers/Dockerfile.classify -t job-applier-classify:test .
```

CDK builds and publishes all three images during deployment. See the pipeline guide
for local container checks and AWS setup.

Collection launch settings live in `config/collections/default.yaml`; the company
directory is `infra/board_sources/job-board-directory.csv`. Edit the YAML's
`boards_s3_uri` to point to your uploaded CSV, then launch a manual run:

```sh
uv run python -m infra.manage collect --config config/collections/default.yaml
```

The YAML stays local: `manage.py` passes its settings to ECS for manual runs, and
CDK captures the same settings in the daily feeder task definition during deployment.
Upload only the CSV to S3 before enabling the schedule. See [collection setup](infra/README.md#board-sources-and-collection-configuration).

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
before more capable document generation. Document generation remains a proposal.
The collection/classification infrastructure is implemented in CDK; its presence
does not mean it has been deployed in your AWS account.

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
- TypeSafe AI Jev classification of stored jobs by seniority, contract type,
  technical relevance and primary role, saved to a separate DynamoDB results table.
- CDK stack with Docker/Fargate workers, SQS retries, filtering indexes and secure key setup.
- Deduplication within a collection using provider, board, region, and posting identity.
- Offline tests for provider formats, pagination, failures, limits, and exports.

There is currently no recommendation model, application submission,
mailbox connection, CV generator or running frontend. LinkedIn
and Welcome to the Jungle fetching have been removed.

## Setup

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
uv sync --locked
```

`uv` creates the local virtual environment and installs the dependencies pinned in
`uv.lock`. Runtime dependencies are HTTPX and Pydantic; Ruff handles linting and
formatting. DynamoDB storage requires AWS authentication and an existing table; see below.
JSON export mode needs no AWS credentials. Collection needs no LLM key; the
separate classification command requires `TYPESAFE_API_KEY`.
Some employer feeds may require their own authorized access.

## DynamoDB setup

For the managed two-table pipeline, deploy the [CDK stack](infra/README.md).
The standalone collection CLI below can also write to an existing table.

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
uv run python -m backend.jobs.cli.collect --boards-file infra/board_sources/job-board-directory.csv \
  --platforms greenhouse lever --max-boards 3 --max-jobs-per-board 5 --total-limit 10 \
  --table job-postings --aws-region eu-west-3
```

Each posting is its own DynamoDB item. Fields such as `title`, `company`,
`description`, `source`, `board`, and `region` are top-level attributes beside
`job_id` and UTC `first_seen`/`last_seen` timestamps; there is no nested `job` map. IDs are SHA-256 hashes of provider,
board, region and source ID (falling back to posting URL). Reruns update existing
items and retain `first_seen`; jobs missing from later runs are not deleted.
Batch collection writes new postings after each company. Writes are not a
transaction: on failure, earlier writes remain and the CLI returns failure.
DynamoDB's 400 KB item limit applies, including descriptions; oversized items
fail explicitly rather than silently losing content. Batch run diagnostics are
printed to the console, not stored as job items. Automatic resume is not implemented.

Existing nested items are flattened when the same posting is fetched again,
preserving its ID and `first_seen`. Classifiers and stream routing accept old
records during this transition. Local code changes do not migrate a deployed
table automatically. Deploy the updated workers/router together, then refresh
boards to rewrite existing items.

Use `--output PATH` to explicitly select the legacy JSON export instead of DynamoDB.
It cannot be combined with `--table`. The examples below illustrate JSON exports.

## Classify jobs already in DynamoDB

The separate classifier assumes the DynamoDB table and postings already exist.
It does not fetch employer boards or write JSON files. It uses TypeSafe AI's
official `typesafe-sdk`, the `jev-latest` model and `POST /v1/systemone`.
See the [official SDK](https://github.com/typesafe-ai/typesafe-sdk-python)
and [API documentation](https://docs.typesafe.ai/go/system-one).

Provide a valid `TYPESAFE_API_KEY` securely in your process environment or secret
manager. No key belongs in source, CLI arguments or Git. Use the standard AWS
credential chain as for collection. Both tables must exist. The identity needs
`dynamodb:DescribeTable`, `dynamodb:Scan`, and `dynamodb:ConditionCheckItem` on the
jobs table, and `dynamodb:DescribeTable`, `dynamodb:GetItem`, `dynamodb:UpdateItem`
and `dynamodb:PutItem` on the results table. The CDK stack supplies worker roles.
Allow network access to `api.typesafe.ai` and your regional DynamoDB endpoint.

```sh
uv sync --locked
uv run python -m backend.jobs.cli.classify \
  --table job-postings --results-table job-classifications --aws-region eu-west-3 --limit 100
```

The default jobs table is `DYNAMODB_TABLE` or `job-postings`; the default results
table is `CLASSIFICATIONS_TABLE` or `job-classifications`. `--model` selects a
different supported Jev model. One System One request asks four typed Choice
questions per posting, using only its title and description:

| Field | Values |
| --- | --- |
| `seniority` | `entry`, `junior`, `mid`, `senior`, or `null` when unspecified |
| `contract_type` | `permanent`, `fixed_term`, `internship`, `apprenticeship`, `freelance`, `temporary`, or `null` when unspecified/ambiguous |
| `is_tech` | `true`, `false`, or `null` when evidence is insufficient |
| `category` | `software_engineer`, `devops_engineer`, `cloud_engineer`, `ai_engineer`, `ml_engineer`, `data_engineer`, `data_scientist`, `security_engineer`, `qa_engineer`, `systems_engineer`, `technical_support`, `technical_management`, `other_tech`, `non_technical`, `unknown` |

The classifier chooses one primary role, one experience level and one contract type.
Seniority and contract type are independent: an internship/apprenticeship label
does not automatically set seniority. Missing experience/contract information is
stored as `null`. Full-time/part-time describe working hours and do not imply a
permanent contract. CDI maps to `permanent`, CDD to `fixed_term`, stage to
`internship`, alternance to `apprenticeship`, and agency/intérim to `temporary`.
Non-technical roles get `is_tech=false` and `category=non_technical`.
Unknown technical relevance gets `is_tech=null` and `category=unknown`.
Invalid or contradictory provider results are rejected, counted and not saved.

Results are stored as separate items in the classifications table, joined to
source jobs by `job_id`. Source job fields are not modified:

```json
{
  "job_id": "<same ID as the source job>",
  "seniority": "senior",
  "contract_type": "permanent",
  "is_tech": true,
  "category": "cloud_engineer",
  "provider": "typesafe",
  "model": "jev-latest",
  "schema_version": 2,
  "input_hash": "<SHA-256 of title and description>",
  "classified_at": "<UTC timestamp>"
}
```

The command follows DynamoDB scan pagination. `--limit` bounds classification
attempts (default 100), not scanned rows; `--page-size` controls scan pages.
Unchanged inputs with matching provider, model and schema version are skipped.
Schema version 2 adds contract type and the four-level seniority contract. Existing
version-1 records are refreshed on their next classification delivery; rescraping
an unchanged posting does not queue it again. Use `infra.manage backfill` after
deploying to queue existing jobs for upgrade. This can incur fresh Jev calls.
Changed titles/descriptions are reclassified. Use `--force` to refresh all results,
including after a model alias such as `jev-latest` changes upstream.
If a collector refreshes a posting, an older classification can remain until
the next classifier run; consumers should compare its input hash before use.

A DynamoDB transaction checks that the posting still exists, its title and description have
not changed during the API call, and the worker still owns the result lease. Conflicting results are discarded, without
resurrecting deleted jobs. Previously written results survive later failures or
interruption. A five-minute per-job lease limits overlapping workers, and current
results are checked again after acquiring the lease. Duplicate calls remain possible
after an external call succeeds but the process dies before saving its result.
The deployed workers use SQS job IDs; this scanning CLI is for bounded backfills.

Network/API errors stop the run; authentication errors are not retried. The SDK
retries rate limits, transient server failures and connection failures up to twice.
Thus each classification attempt can involve up to three HTTP requests.
Missing descriptions are allowed if a title exists, and uncertainty remains unknown.
A scan can read the entire table even if few jobs need classification.

Exit status is `0` on completion without record failures/conflicts, `2` if records
were invalid or changed concurrently, `1` for provider/storage/setup errors, and
`130` on interruption. Counts are printed without job content or API keys.
Classification is tested offline with the real SDK's HTTP transport mocked and
Moto DynamoDB, including a complete CLI request-to-table-write test; live API
accuracy and deployment access are not established by those tests.

## Collect jobs

Fetch from the supplied company-board directory, stopping at 100 jobs total:

```sh
uv run python -m backend.jobs.cli.collect \
  --boards-file infra/board_sources/job-board-directory.csv \
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
| `--max-jobs-per-board 5` | Collect at most five matching jobs per company; default all. |
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
uv run python -m backend.jobs.cli.collect \
  --boards-file infra/board_sources/job-board-directory.csv \
  --platforms greenhouse lever \
  --max-boards 3 --max-jobs-per-board 5 --total-limit 10 \
  --output data/test-jobs.json
```

To apply the existing local text filters:

```sh
uv run python -m backend.jobs.cli.collect "python" \
  --boards-file infra/board_sources/job-board-directory.csv \
  --location Paris --total-limit 100 --output data/python-jobs.json
```

These filters are keyword matching, not semantic recommendations. There is no
`--tech-only` option. Without a query, the catalogue includes non-technical roles;
a cap of 100 therefore means 100 postings, not necessarily 100 relevant tech jobs.

Use `--boards-file infra/board_sources/job-board-directory.csv` for batch runs with the supplied
directory. Use `--help` for the complete CLI interface.

### Fetch a single company

```sh
uv run python -m backend.jobs.cli.collect --source greenhouse --board BOARD_TOKEN --max-jobs-per-board 20
uv run python -m backend.jobs.cli.collect --source lever --board BOARD_TOKEN --region eu
uv run python -m backend.jobs.cli.collect --source ashby --board BOARD_TOKEN --max-jobs-per-board 10
uv run python -m backend.jobs.cli.collect --source workday --board wd1/TENANT/SITE --max-jobs-per-board 10
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
backend/
  jobs/
    models.py                   JobSource, JobPost, SavedJobPost
    collector.py                board loading, batch coordination, global limits
    job_fetchers/               shared base and one adapter per provider
    classification/
      models.py                 classification fields, validation, input fingerprints
      service.py                classification workflow for stored postings
      typesafe.py               Jev integration
    storage/
      jobs.py                   DynamoDB posting persistence
      classifications.py        classification persistence and worker leases
    workers/
      seed.py                   enqueue boards or existing job IDs in AWS
      scraper.py                fetch boards and save job postings
      classifier.py             classify jobs and save results
      runtime.py                shared queue polling and graceful shutdown
    cli/
      collect.py                local collection command, DynamoDB or JSON output
      classify.py               local classification command
  tests/                        offline tests for backend behavior
infra/                          AWS resources and deployment helpers
config/collections/             YAML collection settings
infra/board_sources/            company-board CSV directory
```

All eight fetchers inherit directly from `JobFetcher`. The base class handles
normalization flow, filtering, deduplication, limits, and HTTP lifecycle. Each
provider implements `_rows()` for retrieval/pagination and `_normalize()` for
mapping one record to `JobPost`.

Local commands run with `uv run python -m backend.jobs.cli.collect` and
`uv run python -m backend.jobs.cli.classify`. AWS worker entry points are
`backend.jobs.workers.seed`, `backend.jobs.workers.scraper` and
`backend.jobs.workers.classifier`. Scraping and classification have independent
entrypoints and processing code; shared queue handling lives in `workers/runtime.py`.
Worker code lives in
`jobs/workers/`; fetching, classification, and persistence all belong to `jobs/`.

In batch mode, the collection CLI delegates to `jobs/collector.py`, which chooses fetchers,
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
2. Add per-user profile/preferences filtering using the stored classifications.
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
- [Employer board directory](infra/board_sources/job-board-directory.csv)
