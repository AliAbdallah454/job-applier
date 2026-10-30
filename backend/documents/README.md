# Documents

Tailors your own LaTeX CV to one job and writes a motivation letter. The model does
not build a CV from scratch: it proposes edits to the CV you supply, using the job
description and your profile as extra facts. Any LaTeX template works.

```sh
uv run python -m backend.documents generate --user-id ali --job-id JOB --cv-file cv.tex --region us-east-1
uv run python -m backend.documents status --user-id ali --generation-id ID --region us-east-1
uv run python -m backend.documents retry  --user-id ali --generation-id ID --region us-east-1
```

`--job-file job.json` (title, description, optional company/job_id) replaces `--job-id`
for postings that are not in the Jobs table. `--force` creates a new revision.

## How edits work

The model receives the raw `.tex` and returns `find -> replace` edits (`latex.py`).
Nothing about the template is parsed, so the CV can use any commands. Every edit is
checked on its own; an invalid edit is skipped (and reported) while the others apply:

- `find` must match exactly one place in the document body (after `\begin{document}`);
  whitespace differences are tolerated, and the preamble is never searched or edited
- `replace` may only use LaTeX commands that already appear in the matched snippet, so
  the model can reword and reorder inside your formatting but cannot add packages,
  file access or new layout
- braces must stay balanced and no new unescaped `%`; an edit overlapping an earlier one
  is skipped

The edited CV is compiled with pdfLaTeX, or with XeLaTeX when it uses `fontspec`
(the letter always uses XeLaTeX). The file must be self-contained: multi-file Overleaf
projects (`\input`, custom `.cls`, images) are not supported yet.

## Review

Each generation stores `changes_json` next to the PDFs: every edit with its `find`,
`replace`, `reason`, `applied` and, for skipped edits, the `error`. Edits are checked structurally but not fact-checked, so review
them before sending the CV. A CV that fails to compile marks the generation failed with
the compiler log downloadable through `status`; there is no automatic re-prompt yet.
