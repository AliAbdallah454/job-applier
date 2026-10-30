"""Apply anchored text edits to any LaTeX CV, independent of its template.

The model proposes `find -> replace` edits on the raw source. Each edit must match one
place in the document body, and its replacement may only use LaTeX commands that the
matched snippet already uses: the model can reword and reorder inside the existing
formatting, but cannot add packages, file access or new layout.
"""

import re

from backend.documents.errors import DocumentError

COMMAND = re.compile(r"\\[A-Za-z@]+")
WHITESPACE = re.compile(r"\s+")


def escape(value):
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in str(value)).replace(
        "\n", " "
    )


def validate_cv(source):
    if not all(marker in source for marker in ("\\documentclass", "\\begin{document}")):
        raise DocumentError("Provide a complete LaTeX CV with \\documentclass and a document body")
    if "\\end{document}" not in source:
        raise DocumentError("LaTeX CV has no \\end{document}")
    return source


def engine(source):
    """XeLaTeX for fontspec documents, otherwise pdfLaTeX (Overleaf's default)."""
    uses_fontspec = re.search(r"\\usepackage(\[[^\]]*\])?\{(fontspec|unicode-math)\}", source)
    return "xelatex" if uses_fontspec or "\\setmainfont" in source else "pdflatex"


def unescaped(text, characters):
    return len(re.findall(r"(?<!\\)[" + re.escape(characters) + "]", text))


def balanced(text):
    depth = 0
    for match in re.finditer(r"(?<!\\)[{}]", text):
        depth += 1 if match[0] == "{" else -1
        if depth < 0:
            return False
    return depth == 0


def locate(source, find, body_start, body_end):
    """Find the snippet in the document body: exact match first, otherwise tolerating
    whitespace differences. It must be unique there; the preamble is never matched."""
    words = [re.escape(part) for part in WHITESPACE.split(find.strip()) if part]
    matches = []
    for pattern in (re.escape(find), r"\s+".join(words)) if words else ():
        matches = [m.span() for m in re.compile(pattern).finditer(source, body_start, body_end)]
        if matches:
            break
    if len(matches) != 1:
        found = "is not in the CV body" if not matches else "matches several places"
        raise DocumentError(f"CV edit {found}; it must match exactly one place")
    return matches[0]


def apply_edits(source, edits):
    """Apply every valid edit and skip invalid ones, so one bad edit does not lose the rest.

    Returns the edited source and one report per edit (in the given order) saying whether
    it was applied and, if not, why.
    """
    validate_cv(source)
    body_start = source.index("\\begin{document}") + len("\\begin{document}")
    body_end = source.rindex("\\end{document}")
    spans, reports = [], []
    for edit in edits:
        report = {**edit.model_dump(), "applied": False}
        reports.append(report)
        try:
            start, end = locate(source, edit.find, body_start, body_end)
            original = source[start:end]
            added = set(COMMAND.findall(edit.replace)) - set(COMMAND.findall(original))
            if added:
                raise DocumentError("CV edit adds LaTeX commands that the snippet did not use")
            if not balanced(edit.replace):
                raise DocumentError("CV edit has unbalanced braces")
            if unescaped(edit.replace, "%") > unescaped(original, "%"):
                raise DocumentError("CV edit adds an unescaped % that would hide text")
            # The earlier edit wins when two edits touch the same text.
            if any(start < other_end and other_start < end for other_start, other_end, *_ in spans):
                raise DocumentError("CV edit overlaps an earlier edit")
        except DocumentError as exc:
            report["error"] = str(exc)
            continue
        report["applied"] = True
        spans.append((start, end, edit.replace))

    for start, end, replacement in sorted(spans, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source, reports
