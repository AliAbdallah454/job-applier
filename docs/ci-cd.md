# CI/CD

`.github/workflows/ci-cd.yml` runs on GitHub Actions.

| Event | What runs |
|---|---|
| Pull request | Lint, all test suites, `cdk synth`. No AWS access. |
| Push to `main` (docs/frontend-only pushes are skipped) | The above, then `cdk deploy JobPipeline Application`, then four parallel image builds pushed to ECR. |
| Manual (`workflow_dispatch`) | Same as a push; only deploys when run on `main`. |

## How it fits together

1. **Change detection** is CloudFormation's. `cdk diff` is printed for the log and
   `cdk deploy` only changes what differs. Each push passes `-c imageTag=<commit SHA>`, so
   the ECS task definitions change on every deploy; the tables, queues and repositories do not.
   All four images are rebuilt on every push (Docker layer cache keeps this quick); skipping
   unchanged ones would need per-worker tags and was left out as not worth the complexity.
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
4. Images are tagged with the full commit SHA. Tags are immutable, so a re-run of the same
   commit skips images that were already pushed.

## One-time setup

1. Bootstrap CDK in the account/region if you have not: `npx cdk bootstrap`.
2. Deploy the role (needs admin credentials, once):
   ```sh
   aws cloudformation deploy --stack-name job-applier-github-ci \
     --template-file infra/ci/github-actions-role.yaml --capabilities CAPABILITY_NAMED_IAM
   ```
   Add `--parameter-overrides CreateOidcProvider=false` if the account already has a GitHub
   OIDC provider, and `GitHubRepository=owner/name` if the repository differs.
3. Copy the `DeployRoleArn` output. In GitHub → Settings → Secrets and variables → Actions →
   **Variables**, add `AWS_DEPLOY_ROLE_ARN` and `AWS_REGION` (for example `us-east-1`).
   No access keys are stored.
4. Optional: protect `main` and require the `test` check.

## Caveats

- CI deploys with default CDK context. Any `-c` overrides you use locally (`maxScrapers`,
  `documentModelId`, ...) must live in `cdk.json` or CI resets them.
- A deploy re-applies the template, so it can undo manual changes such as suspended autoscaling.
- Task definitions reference the new SHA tag before the images finish pushing. Services start
  at zero tasks, so this is harmless unless the queue scales workers up in that window; a
  failed push leaves task definitions pointing at a missing tag until the next successful run.
- The first push to `main` after setup is the first deploy; it creates both stacks.
