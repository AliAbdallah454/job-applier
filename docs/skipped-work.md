# Skipped or deferred work

Things deliberately left out for now. Move an item out of this file when it is built.

## Automatic daily job collection (not implemented)

The stack used to create an EventBridge rule (`DailyCollection`, disabled by default) that ran
the feeder task every day, with its settings baked into the task definition from
`config/collections/default.yaml`. That was removed because CDK could not locate the board
directory file on its own: the YAML held a hardcoded S3 URI (a generated bucket name from an
earlier deploy), which tied every deployment to a specific bucket and made the CDK app depend
on a local file at synth time. The YAML now holds only `boards_key` (the object key); `manage.py` takes the bucket from the
stack's `JobSourceBucketName` output for manual runs.

What exists now:

- **Manual collection only:** `uv run python -m infra.manage collect --config config/collections/default.yaml`
  reads the YAML on your machine and passes its settings to the feeder as ECS overrides.
- Upload the board CSV to the bucket named by the `JobSourceBucketName` stack output, under the
  key in the YAML (`job-board-directory.csv`).
- The feeder task role can read the whole job-source bucket the stack creates. A board file in
  any other bucket is not readable.
- The scheduling resources (rule, `ScheduleDLQ` and its alarm) and the `BoardSourceUri`,
  `ScheduleDLQUrl` and `ScheduleRule` outputs no longer exist, nor does the `schedule` and
  `collectionConfig` CDK context.

To bring scheduling back, the feeder needs to find its settings without a local file. The
likely approach is an **SSM parameter** (or a fixed key in the job-source bucket) holding the
collection config, read by the feeder at startup, plus an EventBridge rule that starts the
feeder with no overrides. The board CSV would then be uploaded to the stack's bucket under a
known key. Until then, nothing collects jobs automatically.

## CI/CD

- No ECR lifecycle rule: images accumulate. A "keep the last N" rule is a few lines per repository.
- No release manifest or rollback workflow (see `plans/image-publishing.md`); rollback is
  `cdk deploy -c <worker>ImageTag=<older tag>`.
- The CI runs in a single environment; there are no dev/prod stage separation or approvals.

## Compiling edited CVs on Lambda

Saving a CV edited in the web app (`POST /api/generations/{id}/edit`) compiles it inside the API
process, so TeX must be installed on the machine running the API. That is fine while the API runs
locally for one user, but the API should not compile other users' LaTeX once it is deployed.

The likely approach is a Lambda function built from a container image with TeX, called
synchronously by the API through `Settings.compile_document`, with almost no AWS permissions of
its own. The save flow would not change, and edits would stay off the document queue, because
the user is waiting for the result. It needs:

- a Lambda image, an ECR repository, a CDK function, a CI build step and permission for the API
  to invoke the function
- TeX's working and cache files in `/tmp`, the only writable path, tested with the CV fonts
- a plan for Lambda's 6 MB response limit, which a PDF with large embedded images could exceed

A cold start of such a large image will make the first save after an idle period slower.

## Application

Authentication and the profile API, application submission, per-user recommendations and
Overleaf sync remain unimplemented (see the root README).

## Mail

- Mail sync runs inside the local API, so nothing is read while the API is off. A deployed version
  would need its own scheduled task and a different way to hold the mailbox credentials.
- IMAP with an app password fits one user. Several users would need Gmail and Microsoft OAuth,
  and Gmail's restricted scope needs a Google security review.
- Only `INBOX` is read, and only LinkedIn alert emails become job groups.
