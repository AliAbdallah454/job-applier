# Repository Guidelines

## Project Structure & Module Organization

The Python backend lives in `backend/jobs/`; distributed SQS workers and feeders
live in `backend/pipeline/`. Tests are in `backend/tests/`. AWS CDK infrastructure
lives in `infra/`, with template tests in `infra/tests/` and a stream-routing
Lambda in `infra/stream_router/`. The root Dockerfile packages the workers.
`config/` contains the employer-board registry. See `infra/README.md` for AWS setup.

For new code, group modules by responsibility. Consider `src/` for application code, `tests/` for automated tests, and `docs/` for supporting documentation. Create these directories only when needed. Keep generated output and local configuration separate from tracked source files.

## Build, Test, and Development Commands

Use Python 3.12+, uv and the committed `uv.lock`:

- `uv sync --locked --group infra` installs development/infrastructure tools.
- `uv run python -m unittest discover -s backend/tests -v` runs runtime tests.
- `uv run --group infra python -m unittest discover -s infra/tests -v` tests templates.
- `uv run ruff check .` and `uv run ruff format --check .` validate style.
- `npm ci && npm run synth` generates CloudFormation using pinned CDK.
- `docker build -t job-applier-pipeline:test .` validates the worker image.
Deployment creates billable AWS resources and is separate from synthesis/testing.

When introducing tooling, document exact commands in `README.md`, including dependency installation, local startup, test execution, and production builds where applicable. Commit the relevant dependency manifest and lockfile so contributors can reproduce the environment.

## Coding Style & Naming Conventions

Follow the conventions of the language and framework selected for the project. Use consistent indentation within each file and descriptive names for modules, functions, and variables.

Add formatter and linter configuration when introducing application code. Keep changes focused; avoid mixing functional updates with unrelated formatting or renaming.

## Testing Guidelines

Tests use unittest, Moto and mocked HTTP transports. They must not call live AWS
or TypeSafe APIs. Keep infrastructure assertions around IAM boundaries, secret
handling and queue delivery semantics. Use descriptive test names that identify the behavior and expected outcome. Cover failure paths and regressions as well as successful cases.

## Commit & Pull Request Guidelines

No Git history is available to establish existing commit conventions. Use concise, imperative commit subjects, such as `Add application configuration`.

Pull requests should explain the change, its purpose, and validation performed. Link related issues when available and include screenshots for visible interface changes. Identify any setup steps or new environment variables.

## Security & Configuration

Never commit credentials, tokens, or personal application data. Provide configuration examples with placeholder values and ignore local secrets and generated files.
