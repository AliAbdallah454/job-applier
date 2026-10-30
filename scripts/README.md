# Development scripts

Development tooling and seed fixtures live here, separately from backend
application code. Scripts import backend models for validation; the backend does
not import these scripts or include their fixtures in runtime images.

```text
scripts/
├── seed_profiles.py
└── tests/test_seed_profiles.py
```

## Seed or reset demo profiles

After deploying the Application stack, run from the repository root with your
AWS environment loaded:

```sh
uv run python -m scripts.seed_profiles --stack Application --region us-east-1
```

The script discovers the table through the stack's `ProfilesTable` output.
It validates the complete fixture, deletes existing `demo-user-*` profiles and
inserts eight fictional profiles plus the supplied personal profile with `user_id:
"ali"`. The named `ali` record is replaced only when it is included in the selected
fixture; all other user IDs are preserved. Repeating the command resets edits and
removes obsolete demo users. This fixture contains supplied personal data and is
intended for your development stack.

Fixtures cover all six career sections with fixed entry IDs, one to three
experiences per user, and varied technical backgrounds. Edit
`data/fake-data/demo_profiles.json` to change them, or pass `--file PATH` for another
non-empty JSON array. All fixture user IDs must use the reserved `demo-user-`
prefix, with `ali` as an explicit development seed exception. Never use the demo
prefix for authenticated production accounts. The seed ID `ali` is a development
identifier, not an authentication identity.

Use `--stack` for your development stack and `--profile` to select AWS credentials.
This writes to real DynamoDB; use development resources. The reset is not atomic,
so rerun after a failed or interrupted write. Documents and job records are not
changed. Required permissions are CloudFormation `DescribeStacks` and DynamoDB
`DescribeTable`, `Scan` and `BatchWriteItem`.

## Export junior tech jobs

```sh
uv run python -m scripts.fetch_junior_jobs --region us-east-1 --output junior-jobs.csv
```

Reads the `JobPipeline` stack's `JobsTable` and `ClassificationsTable` outputs and
writes a CSV of tech jobs classified as `entry` or `junior` whose contract type is
not `internship` (unknown contract types are kept), newest first. Jobs whose posting
changed since classification are skipped. Read-only; needs CloudFormation
`DescribeStacks` and DynamoDB `Query` and `BatchGetItem`.

## Offline checks

```sh
uv run python -m unittest discover -s scripts/tests -v
uv run ruff check scripts
uv run ruff format --check scripts
```
