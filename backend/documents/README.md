# Documents

Tailors your own LaTeX CV to one job and writes a motivation letter (why you want the role)
and a cover letter (why you fit it), in English or French. In French the CV is translated too,
while the model's list of changes stays in English for your review. A French CV compiled with
pdfLaTeX and without `fontenc` also gets `\usepackage[T1]{fontenc}` and `lmodern` after
`\documentclass`; the default encoding builds é from two glyphs, which reads as ´e in the PDF text. The model does
not build a CV from scratch: it rewrites the body of the CV you supply, using the job
description and your profile as extra facts. Any LaTeX template works.

```sh
uv run python -m backend.documents generate --user-id ali --job-id JOB --cv-file cv.tex --region us-east-1
uv run python -m backend.documents status --user-id ali --generation-id ID --region us-east-1
uv run python -m backend.documents retry  --user-id ali --generation-id ID --region us-east-1
```

`--job-file job.json` (title, description, optional company/job_id) replaces `--job-id`
for postings that are not in the Jobs table. `--force` creates a new revision.
`--model ID` picks the model.

## Models

`DOCUMENT_MODELS` in `models.py` lists the models you can pick; the first is the default.
The API, web app, CLI and worker all use that list, and deploying gives the worker IAM
access to each one, so adding a model is a one-line change plus a deploy. The chosen
model is frozen in the generation's input snapshot: `retry` uses the same one, and another
model on the same inputs creates a new generation. Only Claude and Nova Pro IDs are
accepted (the Nova schema is adapted in `bedrock.py`, and it gets a lower `maxTokens`).

## How the rewrite works

The model receives the raw `.tex` and returns the whole new document body, plus a list of
its changes (`latex.py`). Nothing about the template is parsed, so the CV can use any
commands. Your preamble is kept byte for byte; `\begin{document}` and anything before it
in the answer are dropped. A rewrite is rejected when it:

- is unchanged from your CV (whitespace is ignored)
- uses a LaTeX command or environment the original body does not use, so the model can
  reword and reorder inside your formatting but cannot add file access or new layout
- has unbalanced braces or a new unescaped `%`

A rejected rewrite, a compile error or malformed model output goes back to the model
with the error, for up to 3 attempts in one run. After the third the generation is
`failed`, with the compiler log downloadable through `status` when compilation was the
problem. A failed `retry` that has a saved rewrite tries that one first.

A user can also edit the CV source of a ready generation (`POST /api/generations/{id}/edit`).
The API compiles it and `save_edit` updates the same record directly: no queue, worker or
model. The generation id, `created_at` and `job_created` stay the same, so the job still has
one entry. It keeps the letters, keywords and the model's explanations, and none of the checks
above apply, since the compiler decides. The new files go under `edits/<uuid>/`, and `diff` is
always computed against the frozen original CV.

The CV an edit replaces is appended to the record's `cv_versions`, oldest first, as its file
references and `saved_at` (its `updated_at`), so version 1 is the model's CV. A record the old
edit flow left behind has `edited_from` and its first CV is already the user's; the API reports
that as `starts_from_edit`, and nothing writes the attribute any more. The request carries the
`updated_at` its draft started from, and a different one gets a 409 before anything compiles.
The update is conditional on `status` and that `updated_at`: of two saves from the same read,
the second gets a 409 instead of dropping a version. A compile failure saves nothing.

`regenerate` does not use edits: it makes a separate generation from the frozen inputs, with
no version history.

The letters are edited the same way, through the same endpoint with `document` set to
`motivation_letter` or `cover_letter`. A letter edit replaces only that letter, and its old files
go to `<document>_versions`. `revision` is that document's own count of saves, so saving one
document does not invalidate a draft of another. A letter generated before the font fallback names
DejaVu Sans directly, so editing it needs that font where the API runs.

Compiling with `-synctex=1` also writes a file linking PDF positions to source lines, stored as
the `cv_synctex` artifact for the current CV. `GET /api/generations/{id}/source-line` runs the
`synctex` tool on it, so that tool must be installed where the API runs. A CV compiled before this
has no such file until its next edit.

The edited CV is compiled with pdfLaTeX, or with XeLaTeX when it uses `fontspec`
(the letters always use XeLaTeX, with DejaVu Sans or, where that is missing, Helvetica). The file must be self-contained: multi-file Overleaf
projects (`\input`, custom `.cls`, images) are not supported yet.

## Review

Each generation stores `changes_json` next to the PDFs:

- `diff`: `before`/`after` word-level differences computed from the two sources, so
  nothing can be left out
- `explanations`: the model's own `change` and `reason` list, which can be incomplete
  or wrong

The rewrite is checked structurally but not fact-checked, so review the diff before
sending the CV. The model can also return a wrong list of changes: trust `diff`.

Generations made before prompt version 7 hold a list of find/replace edits instead; the
API shows them with no changes.
