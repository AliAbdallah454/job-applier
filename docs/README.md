# Documentation Map

For a new contributor or LLM session, start with [AGENTS.md](../AGENTS.md) and [CLAUDE.md](../CLAUDE.md).

| Document | Purpose | Status |
|---|---|---|
| [Contributor guidelines](../AGENTS.md) | Repository conventions | Existing instructions; directory overview has older entries |
| [Direct image publishing plan](plans/image-publishing.md) | Separate infrastructure provisioning from CI/CD publishing | Repositories and direct-push contract implemented; release manifest and CI workflow not yet |
| [Infrastructure guide](../infra/README.md) | Stack outputs and current deployment/operations | Repositories, push workflow and image tags |
| [CI/CD](ci-cd.md) | GitHub Actions deploy, image push and one-time AWS setup | Written, not yet run |
| [Skipped work](skipped-work.md) | Deliberately unimplemented items, including scheduled collection | Living list |
| [Documents guide](../backend/documents/README.md) | Tailoring your LaTeX CV and letter generation, retries and review | Current implementation |
| [Fetcher guide](fetchers.md) | Employer-board adapter behavior and limitations | Feature reference |
| [Web app guide](../frontend/README.md) | Run the local API and React app; hosting plan | Current implementation; local only, not deployed |
| [Development scripts](../scripts/README.md) | Profile fixtures and reset behavior | Current implementation |

When changing behavior, update the relevant feature guide.
Keep proposed architecture labelled as proposed until the code implements it.
Never mark a feature deployed based only on tests or synthesized templates.
