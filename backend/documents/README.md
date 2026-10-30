# Documents

Tailors your own LaTeX CV to one job and writes a motivation letter. The model does
not build a CV from scratch: it suggests structured edits to the CV you supply, using
the job description and your profile as extra facts.

```sh
uv run python -m backend.documents generate --user-id ali --job-id JOB --cv-file cv.tex --region us-east-1
uv run python -m backend.documents status --user-id ali --generation-id ID --region us-east-1
uv run python -m backend.documents retry  --user-id ali --generation-id ID --region us-east-1
```

`--job-file job.json` (title, description, optional company/job_id) replaces `--job-id`
for postings that are not in the Jobs table. `--force` creates a new revision.

## Supported CVs

`latex.py` is the LaTeX adapter. It recognizes Jake-style resume macros
(see `examples/jake_cv.tex`):

- entries: `\resumeSubheading{4 args}`, `\resumeSubSubheading{2}`, `\resumeProjectHeading{2}`
- bullets: `\resumeItem{text}`, attached to the entry above them
- skills: `\textbf{Group}{: Skill, Skill, ...}` in a section without entries

A CV with none of these is rejected before anything is queued. Comments are ignored.

## What the model can change

- reorder or drop entries in a section (education is left alone by the prompt)
- reorder, drop, rewrite or add bullets (added bullets must come from the profile)
- reorder, drop or add skills (added skills must exist in the CV or the profile)

Edits are applied by replacing only those spans of your source; the preamble, header,
layout and everything else stay byte for byte. Inserted text is LaTeX-escaped. A kept
bullet whose text is unchanged keeps its original LaTeX formatting; a rewritten one
becomes plain text.

## Review

Each generation stores `changes_json` next to the PDFs: every rewritten bullet
(before/after), added bullet, dropped entry and dropped bullet. Rewrites are checked
structurally but not fact-checked, so review it before sending the CV.
