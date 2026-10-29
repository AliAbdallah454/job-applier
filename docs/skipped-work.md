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

- Images are all rebuilt on every push to `main`; skipping unchanged images (for example
  content-hash tags) was considered and dropped as not worth the complexity.
- No ECR lifecycle rule: images accumulate. A "keep the last N" rule is a few lines per repository.
- No release manifest or rollback workflow (see `plans/image-publishing.md`); rollback is
  `cdk deploy -c imageTag=<older sha>`.
- The CI runs in a single environment; there are no dev/prod stage separation or approvals.

## Application

Authentication and the profile API, application submission, mailbox integration, per-user
recommendations and Overleaf sync remain unimplemented (see the root README).
