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


def locate(source, find, body_start):
    """Exact match first; otherwise tolerate whitespace differences. Must be unique."""
    matches = [m.span() for m in re.finditer(re.escape(find), source)]
    if not matches:
        words = [re.escape(part) for part in WHITESPACE.split(find.strip()) if part]
        pattern = r"\s+".join(words)
        matches = [m.span() for m in re.finditer(pattern, source)] if words else []
    if len(matches) != 1:
        found = "is not in the CV" if not matches else "matches several places"
        raise DocumentError(f"CV edit {found}; it must match exactly one place")
    start, end = matches[0]
    if start < body_start:
        raise DocumentError("CV edits may only change the document body, not the preamble")
    return start, end


def apply_edits(source, edits):
    """Check every edit, then apply them all; any invalid edit rejects the whole set."""
    validate_cv(source)
    body_start = source.index("\\begin{document}") + len("\\begin{document}")
    body_end = source.rindex("\\end{document}")
    spans = []
    for number, edit in enumerate(edits, 1):
        try:
            start, end = locate(source, edit.find, body_start)
            original = source[start:end]
            if end > body_end:
                raise DocumentError("CV edits may only change the document body")
            added = set(COMMAND.findall(edit.replace)) - set(COMMAND.findall(original))
            if added:
                raise DocumentError("CV edit adds LaTeX commands that the snippet did not use")
            if not balanced(edit.replace):
                raise DocumentError("CV edit has unbalanced braces")
            if unescaped(edit.replace, "%") > unescaped(original, "%"):
                raise DocumentError("CV edit adds an unescaped % that would hide text")
        except DocumentError as exc:
            raise DocumentError(f"Edit {number}: {exc}") from None
        spans.append((start, end, edit.replace))

    spans.sort()
    for (_, previous_end, _), (start, _, _) in zip(spans, spans[1:], strict=False):
        if start < previous_end:
            raise DocumentError("CV edits overlap")
    for start, end, replacement in reversed(spans):
        source = source[:start] + replacement + source[end:]
    return source
