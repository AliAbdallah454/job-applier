# Jobs browser

A local, read-only browser for the existing Jobs and Classifications tables.
All browser files and the local Python HTTP helper live in this folder. There are
no frontend build dependencies and no AWS credentials in client-side JavaScript.

## Start

From the repository root, install Python dependencies and load your AWS environment:

```sh
uv sync --locked
set -a
source .env
set +a
uv run python -m frontend.server --region us-east-1
```

Open **http://127.0.0.1:8000**. The server discovers table names from the `JobPipeline`
CloudFormation outputs. Use `--stack` for another stack or `--profile` to select
another AWS profile. The normal AWS credential chain handles authentication;
refresh your AWS login if the session has expired.

Alternatively, supply both table names without CloudFormation discovery:

```sh
uv run python -m frontend.server --region us-east-1 \
  --jobs-table YOUR_JOBS_TABLE --classifications-table YOUR_CLASSIFICATIONS_TABLE
```

Preview with clearly labelled example jobs and no AWS access:

```sh
uv run python -m frontend.server --demo
```

Use `--port 8001` if port 8000 is occupied. Press Ctrl+C to stop. The server binds
to localhost and has no user authentication; it is a local development tool.
It is not deployed by the current CDK stack.

## Filters and pagination

- Defaults to tech jobs with entry or junior seniority.
- Filter technical relevance, experience levels, role category and contract type.
- Search title, company, location and description; all search words must match.
- Selecting no experience levels includes every level, including unspecified.
- Select a card to read its description and open the original posting.
- **Load more** continues the same search. The count is loaded matches, not a
  full-table total. A filtered DynamoDB page can be empty while more pages remain.

The helper queries `ByTechSeniority`, applies classification filters, and batch-reads
the associated postings. It skips deleted postings and classifications whose input
hash no longer matches the current title/description. Older results without contract
type appear as unspecified when otherwise compatible. Index reads are eventually
consistent. These reads consume DynamoDB capacity; the browser never writes data,
calls Jev or launches workers.

Your AWS identity needs `dynamodb:Query` on the Classifications index,
`dynamodb:BatchGetItem` on Jobs and, for automatic discovery,
`cloudformation:DescribeStacks`.

## Verify

```sh
uv run python -m unittest discover -s frontend/tests -v
uv run ruff check frontend
uv run ruff format --check frontend
node --check frontend/app.js
```
