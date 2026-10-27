# Repository Guidelines

## Project Structure & Module Organization

This repository currently contains no application code, tests, assets, or build configuration. Establish the directory layout alongside the first implementation and update this guide to match it.

For new code, group modules by responsibility. Consider `src/` for application code, `tests/` for automated tests, and `docs/` for supporting documentation. Create these directories only when needed. Keep generated output and local configuration separate from tracked source files.

## Build, Test, and Development Commands

No installation, development, build, or test commands are configured yet. Do not assume a particular language, package manager, or framework.

When introducing tooling, document exact commands in `README.md`, including dependency installation, local startup, test execution, and production builds where applicable. Commit the relevant dependency manifest and lockfile so contributors can reproduce the environment.

## Coding Style & Naming Conventions

Follow the conventions of the language and framework selected for the project. Use consistent indentation within each file and descriptive names for modules, functions, and variables.

Add formatter and linter configuration when introducing application code. Keep changes focused; avoid mixing functional updates with unrelated formatting or renaming.

## Testing Guidelines

No testing framework or coverage threshold exists yet. Introduce automated tests with the first testable behavior and document how to run them. Use descriptive test names that identify the behavior and expected outcome. Cover failure paths and regressions as well as successful cases.

## Commit & Pull Request Guidelines

No Git history is available to establish existing commit conventions. Use concise, imperative commit subjects, such as `Add application configuration`.

Pull requests should explain the change, its purpose, and validation performed. Link related issues when available and include screenshots for visible interface changes. Identify any setup steps or new environment variables.

## Security & Configuration

Never commit credentials, tokens, or personal application data. Provide configuration examples with placeholder values and ignore local secrets and generated files.
