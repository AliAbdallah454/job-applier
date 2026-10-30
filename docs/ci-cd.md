# CI/CD

`.github/workflows/ci-cd.yml` runs on GitHub Actions.

| Event | What runs |
|---|---|
| Pull request | Lint, all test suites, `cdk synth`. No AWS access. |
| Push to `main` (docs/frontend-only pushes are skipped) | The above, then `cdk deploy JobPipeline Application`, then parallel builds of only the images whose contents changed. |
| Manual (`workflow_dispatch`) | Same as a push; only deploys when run on `main`. |

`.github/workflows/frontend.yml` runs separately on pull requests and pushes to `main` that
touch `frontend/` or `backend/`: type check, Vitest, production build, and a check that
`frontend/openapi.json` and `frontend/src/api/schema.d.ts` match the API. It has no AWS access.
`backend/api` is copied into worker images by `COPY backend/` but no worker runs it, so it is
not part of any image tag.

## How it fits together

1. **Image tags are content hashes.** `scripts/image_tag.sh <Dockerfile>` hashes the
   Dockerfile, `.dockerignore`, `pyproject.toml`, `uv.lock` and a fixed list of code folders
   per image (Markdown excluded):

   | Image | Code folders |
   |---|---|
   | documents | `backend/documents`, `backend/profiles`, `backend/utils` |
   | seed, scraper, classifier | `backend/jobs`, `backend/utils` |

   The deploy passes each worker its own tag (`-c seedImageTag=...` etc.). If an image's inputs
   did not change, its tag, and so its ECS task definition, stays the same, and the build is
   skipped because that tag is already in ECR. A `backend/documents` change rebuilds only the
   documents image; a `backend/jobs` change rebuilds the three job images; infra-, test- and
   docs-only pushes rebuild nothing. `cdk diff` is printed for the log and `cdk deploy` only
   changes what differs.
2. **Infrastructure deploys first** because the ECR repositories live in the stacks and must
   exist before the first push.
3. **Dockerfile ↔ repository link** is the `matrix.include` list in the workflow:

   | Repository | Dockerfile |
   |---|---|
   | `jobpipeline/seed` | `containers/Dockerfile.seed` |
   | `jobpipeline/scraper` | `containers/Dockerfile.scrape` |
   | `jobpipeline/classifier` | `containers/Dockerfile.classify` |
   | `application/documents` | `containers/Dockerfile.documents` |

   Add a worker by adding a matrix entry and a repository in the stack.
4. Tags are immutable, so re-running a commit never pushes twice. To find the commit behind a
   tag, compare the image's push time in ECR with the workflow runs.

## One-time setup

1. Bootstrap CDK in the account/region if you have not: `npx cdk bootstrap`.
2. Deploy the role (needs admin credentials, once):
   ```sh
   aws cloudformation deploy --stack-name job-applier-github-ci \
     --template-file infra/ci/github-actions-role.yaml --capabilities CAPABILITY_NAMED_IAM
   ```
   Add `--parameter-overrides CreateOidcProvider=false` if the account already has a GitHub
   OIDC provider, and `RepositorySubject=owner@ID/repo@ID` if the repository differs. GitHub's OIDC subject includes numeric owner and repo IDs; if the role is refused, read the real subject from the failed `AssumeRoleWithWebIdentity` event in CloudTrail (`userName` field).
3. Copy the `DeployRoleArn` output. In GitHub → Settings → Environments, create the
   environment `ci_cd_deploy_env` and add two **environment variables**: `AWS_DEPLOY_ROLE_ARN`
   and `AWS_REGION` (for example `us-east-1`). No access keys are stored. The deploy and push
   jobs declare this environment, which is how they see these values and how the role knows
   to trust them (OIDC subject `repo:<owner>@<id>/<repo>@<id>:environment:ci_cd_deploy_env`).
4. Optional: on the environment, restrict deployment branches to `main` and/or add required
   reviewers; require the `test` check on `main`.

## Caveats

- CI deploys with default CDK context. Any `-c` overrides you use locally (`maxScrapers`,
  `documentTimeoutSeconds`, ...) must live in `cdk.json` or CI resets them.
- A deploy re-applies the template, so it can undo manual changes such as suspended autoscaling.
- Task definitions reference a changed image's new tag before it finishes pushing. Services
  start at zero tasks, so this is harmless unless the queue scales workers up in that window;
  a failed push leaves that task definition pointing at a missing tag until the next run
  (which rebuilds it, because the tag is still absent from ECR).
- The worker list appears twice in the workflow (the tag step and the matrix); keep them in
  step when adding a worker.
- The folder list is maintained by hand; add a folder there when a worker starts importing
  from it. Documents and profiles must not import from `backend/jobs` (and vice versa); code
  both sides need goes in `backend/utils`.
- The first push to `main` after setup is the first deploy; it creates both stacks.
