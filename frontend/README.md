# Web app

Browse classified jobs, keep your profile and LaTeX CVs, tailor a CV to a job and review
the result. React, TypeScript, Vite and Tailwind talk to the FastAPI app in `backend/api/`.
Both run on your machine. Nothing here is deployed, and the API has no login, so it only
binds to localhost and refuses writes from other sites.

## Start

Two terminals, from the repository root:

```sh
uv sync --locked
uv run python -m backend.api --region us-east-1
```

```sh
cd frontend
npm ci
npm run dev
```

Open **http://localhost:5173**. Vite forwards `/api` to port 8000, and the API uses your
normal AWS login, so credentials never reach the browser. API options: `--user-id` (default
`ali`), `--profile`, `--port`, `--application-stack`, `--jobs-stack`.

To try everything without AWS, Bedrock or TeX:

```sh
uv run python -m backend.api --demo
```

Demo mode runs the real routes and document service on in-memory AWS with a fake model and
compiler, and forgets everything when stopped. The job marked "compile failure example"
shows the failure page.

## Tailoring

**Tailor my CV** on a job snapshots your CV, profile and the job, then puts a message on the
document queue. The generation page polls until a worker finishes. Workers start from zero,
so a run can stay queued for a few minutes. Identical inputs return the existing generation;
**Regenerate** makes a new, separate revision from the same frozen inputs, so it starts from
your uploaded CV and not from your edits, and **Retry** re-queues a failed run or one whose
worker died. Each real run calls Bedrock and costs money.

A profile is required first. CVs must be one self-contained UTF-8 `.tex` file up to 200 KB.

The generation page shows the model's list of edits and a diff computed from the two CV
sources. Rewrites are checked for structure but not fact-checked, so read each change before
sending a CV.

**Edit LaTeX** beside the preview edits the whole CV source. Saving compiles it in the API and
updates the same generation straight away, with no queue, worker or Bedrock call, so the machine
running the API needs TeX Live (`--demo` does not). The job keeps one entry, which always holds your
latest CV: its id and date stay the same. The letter, keywords and the model's list of edits are
kept, and the diff compares the original CV with your version. If it does not compile, nothing is
saved and your draft stays open beside the error. If the same CV was saved elsewhere after your draft
began, you get a 409, the page shows that newer CV, your draft stays, and saving again replaces it. A
draft that was not saved is lost when the page is left.

Each CV an edit replaces appears under **Previous versions** in the review list, newest first, with
its PDF and `.tex`. Version 1 is the model's, unless the entry came from the old edit flow, which
gave each edit its own entry. Old versions can be opened but not restored.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/options` | Filter values, current user, demo flag |
| `GET /api/jobs`, `GET /api/jobs/{id}` | Filters: `tech`, `seniority`, `category`, `contract`, `q`, `cursor` |
| `GET`/`PUT /api/profile` | `PUT` must carry the loaded `version`, or it returns 409 |
| `GET`/`POST /api/cvs`, `DELETE /api/cvs/{id}` | CVs at `users/<id>/cvs/<cv_id>/<filename>.tex` |
| `GET`/`POST /api/generations` | History (`job_id`, `status`) with file names, company and job link; start a run |
| `GET /api/generations/{id}` | Status, keywords, edits, file names, compile log, previous versions |
| `POST /api/generations/{id}/retry`, `/regenerate` | Re-queue, or make a new revision |
| `POST /api/generations/{id}/edit` | Compile `cv_latex` and save it over the current CV. Must carry the `updated_at` the draft started from, or it returns 409; 422 with the error if it does not compile |
| `GET /api/generations/{id}/files/{name}` | Current PDF, source or log; add `?download=true` to save it under a name from the job title. An edit changes what this URL returns, so it is sent as `no-cache`, and previews and CV downloads add `v=<updated_at>` |
| `GET /api/generations/{id}/versions/{number}/files/{name}` | `cv_pdf`, `cv_tex` or `changes_json` of a replaced CV, from number 1; takes `?download=true` too, and these never change, so they can be cached |

Search filters one scanned page at a time, so a page can be empty while more remain; the
app keeps loading automatically, up to ten empty pages in a row. The count is loaded
matches, not a total. History reads all of your generations and sorts them in the API. The
Tailored CVs page offers the CV, the motivation letter and the job posting on each row, so
nothing needs opening first. The job link and company are read from the Jobs table when
listing, so a removed job has neither.

Scrapers flatten most descriptions to one line (`html_to_text`), and storing the original
layout would change every classification hash. So `src/pages/jobText.ts` rebuilds headings,
paragraphs and lists when displaying a job. It only regroups the words, and it is a best guess:
a list with no punctuation can split in the wrong place.

Your AWS identity needs CloudFormation `DescribeStacks`, DynamoDB read and write on the
tables, S3 read, write, list and delete under `users/*`, and `sqs:SendMessage` on the
document queue.

## Change the API

Pydantic is the source of truth. After changing a route or model:

```sh
cd frontend
npm run types
```

This rewrites `openapi.json` and `src/api/schema.d.ts`; commit both. CI fails if they are
out of date.

## Verify

```sh
uv run python -m unittest backend.tests.test_api backend.tests.test_catalogue
cd frontend && npm run lint && npm test && npm run build
```

## Later (proposed, not implemented)

Host the build on S3 and CloudFront, run the API on Lambda or ECS with its own image
(`containers/Dockerfile.api`, `uv sync --no-dev --group api`, and an entry in
`scripts/image_tag.sh` for `backend/api`, `backend/documents`, `backend/profiles`,
`backend/jobs` and `backend/utils`), and add Cognito by replacing `deps.current_user`.
Replace the local host and origin guards in `app.py` at the same time.
