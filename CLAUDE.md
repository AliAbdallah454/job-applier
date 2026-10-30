# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read `AGENTS.md` (conventions) and `docs/README.md` (doc map) first. `AGENTS.md`'s directory overview is partly outdated; use the paths below. Proposed architecture (e.g. the CI/manifest parts of `docs/plans/image-publishing.md`) is **not implemented** — don't treat plans as code, and don't mark anything "deployed" based on tests or synthesized templates.

## Commands

Python 3.12+, `uv`, committed `uv.lock`.

```sh
uv sync --locked --group infra                       # dev + CDK deps
uv run python -m unittest discover -s backend/tests -v          # runtime tests
uv run python -m unittest backend.tests.test_documents.SomeClass.test_name   # single test
(cd frontend && npm ci && npm test && npm run build)            # web app tests and build
uv run python -m unittest discover -s scripts/tests -v          # seed script and image tag tests
uv run --group infra python -m unittest discover -s infra/tests -v   # CDK template tests
uv run ruff check . && uv run ruff format --check .             # lint (line length 100; E,F,I,UP,B)
npm ci && npm run synth                                         # CloudFormation synth (pinned CDK)
docker build --platform linux/amd64 -f containers/Dockerfile.<seed|scrape|classify|documents> -t job-applier-<name>:test .
```

Operational entry points:

```sh
uv run python -m infra.manage collect --config config/collections/default.yaml   # launch a collection run on ECS
uv run python -m backend.api --region us-east-1   # local API on :8000 (--demo needs no AWS or Bedrock)
(cd frontend && npm run dev)                      # web app on :5173, proxies /api to :8000
(cd frontend && npm run types)                    # regenerate openapi.json and schema.d.ts after API changes
uv run python -m backend.documents generate --user-id U --job-id J --cv-file cv.tex --model ID --region us-east-1   # also: status, retry
uv run python -m scripts.seed_profiles --stack Application --region us-east-1
```

Tests use `unittest`, Moto and mocked HTTP transports; they must never call live AWS or TypeSafe. Deployment creates billable resources and is separate from synth/test.

## Architecture

Two independently deployable CDK stacks (`infra/stack.py` = `JobPipeline`, `infra/application_stack.py` = `Application`; entry `infra/app.py`). Physical table/bucket/queue names come from CloudFormation outputs — never hardcode them.

**Job pipeline** (`backend/jobs/`): S3 board directory → feeder (`workers/seed.py`) → SQS scrape queue → ECS scrapers (`workers/scraper.py`, using `job_fetchers/` adapters for Greenhouse, Lever, Workday, Ashby, Workable, Recruitee, Rippling, Personio, normalized into `models.JobPost`) → Jobs DynamoDB table → DynamoDB stream → Lambda (`infra/stream_router/`) → SQS classify queue → ECS classifiers (`workers/classifier.py`, TypeSafe/Jev) → separate Classifications table. Classification is keyed on content hash so refreshing `last_seen` must not trigger another paid classification. Jobs are direct DynamoDB items keyed by `job_id`.

**Documents** (`backend/documents/`): tailors the user's own LaTeX CV (any template) to a job. CV source + profile (`backend/profiles/models.py`, extra facts) + job → frozen versioned S3 snapshot + generation record `(user_id, generation_id)` → SQS → ECS document worker → Bedrock (the model chosen per request from `DOCUMENT_MODELS` in `models.py`, frozen in the snapshot; Nova's tool schema adapted in `bedrock.py`) returns the rewritten document body, a list of its changes and the letter. `latex.py` keeps the preamble and checks the body without parsing the template (not unchanged, only commands the original body uses, balanced braces); a rejected rewrite, compile error or malformed output goes back to the model, up to 3 attempts in `service.py`, then the generation fails. Each validated output is checkpointed to S3 → compiled (`render.py`; pdfLaTeX, or XeLaTeX when the CV uses fontspec) → S3 artifacts, including `changes.json` (`diff` computed from the sources, `explanations` from the model; not fact-checked). Inputs are frozen per request; `retry` reuses it, changed inputs create a new generation, `--force` a new revision. A lease prevents concurrent generation. SQS is at-least-once; ack only after success; failures go to a DLQ.

**API and web app** (`backend/api/`, `frontend/`, local only, not deployed): FastAPI in its own folder because it imports both jobs and documents; handlers get the user from `deps.current_user` (config user today, the single place to add Cognito). CVs live in S3 at `users/<id>/cvs/<cv_id>/<filename>.tex` with no table. Files are streamed by the API, so the browser needs no presigned URLs or bucket CORS. `--demo` runs the real routes and `DocumentService` on Moto with a fake model and compiler (`demo.py`). `frontend/` is React, TypeScript, Vite and Tailwind with its own `package.json`; its types are generated from FastAPI's OpenAPI, so Pydantic stays the source of truth. TypeScript stays on 5.x because `openapi-typescript` does not work with 7.

`backend/utils/runtime.py` is the shared SQS polling/ack/visibility-renewal loop used by the job workers and the document worker. `backend/utils/job_taxonomy.py` holds the job labels (`Seniority`, `ContractType`, `JobCategory`) used by classification and profile preferences. Keep `backend/documents`/`backend/profiles` free of `backend/jobs` imports (and vice versa): image rebuild tags rely on that folder split. Workers run in ECS at desired count 0 with Application Auto Scaling (source defaults: 8 scrapers, 8 classifiers, 2 document workers via `maxScrapers`/`maxClassifiers`/`maxDocumentWorkers` context). Each worker has its own Dockerfile in `containers/` and a CDK-created ECR repo (`jobpipeline/{seed,scraper,classifier}`, `application/documents`, created by `worker_image()` in each stack). CDK never builds or pushes images: push manually/CI, then deploy with `-c imageTag=<tag>` (or `<worker>ImageTag`); default tag is the placeholder `unpublished`.

The API dependencies are in the `api` dependency group, included by `dev`, so `--no-dev` worker images do not install them. CI/CD is `.github/workflows/ci-cd.yml` plus `frontend.yml` (see `docs/ci-cd.md`): on push to main it deploys both stacks with per-worker content-hash tags (`scripts/image_tag.sh` hashes the Dockerfile, lockfiles and a hard-coded list of code folders per image: documents → `backend/documents` + `backend/profiles` + `backend/utils`, job workers → `backend/jobs` + `backend/utils`; passed as `-c <worker>ImageTag=`), then builds and pushes only images whose tag is not yet in ECR; the workflow matrix is the Dockerfile↔repository mapping. The IAM role template is `infra/ci/github-actions-role.yaml`.

Collection settings live in `config/collections/default.yaml` (local only: `manage.py collect --config` passes it to ECS as overrides; CDK does not read it and there is no scheduled collection, see `docs/skipped-work.md`); board list is `infra/board_sources/job-board-directory.csv` uploaded to S3.

## Notes

- Runtime code is grouped by feature; dev scripts/fixtures stay outside `backend/`. Don't add layers/services without need.
- Collection needs no LLM key; classification needs `TYPESAFE_API_KEY`. Never commit `.env` or credentials.
- Application submission, mailbox integration and recommendations are not implemented; a generated document package is not a submitted application.
- When behavior changes, update the relevant feature README.
