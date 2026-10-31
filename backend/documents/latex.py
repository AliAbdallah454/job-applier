"""Check a model's rewrite of any LaTeX CV, independent of its template.

The model returns the whole document body. The user's preamble is kept byte for byte, and
the new body may only use LaTeX commands and environments the original body already uses:
the model can reword and reorder inside the existing formatting, but cannot add packages,
file access or new layout.
"""

import difflib
import re

from backend.documents.errors import DocumentError

BEGIN = "\\begin{document}"
END = "\\end{document}"
COMMAND = re.compile(r"\\begin\{[^}]*\}|\\[A-Za-z@]+")
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
    if not all(marker in source for marker in ("\\documentclass", BEGIN)):
        raise DocumentError("Provide a complete LaTeX CV with \\documentclass and a document body")
    if END not in source:
        raise DocumentError("LaTeX CV has no \\end{document}")
    return source


def engine(source):
    """XeLaTeX for fontspec documents, otherwise pdfLaTeX (Overleaf's default)."""
    uses_fontspec = re.search(r"\\usepackage(\[[^\]]*\])?\{(fontspec|unicode-math)\}", source)
    return "xelatex" if uses_fontspec or "\\setmainfont" in source else "pdflatex"


def with_accented_text(source):
    """Give a pdfLaTeX CV an encoding with accented glyphs, or its PDF text reads ´e to an ATS.

    After \\documentclass, so that a font the CV chooses still wins over Latin Modern.
    """
    if engine(source) == "xelatex" or "{fontenc}" in source:
        return source
    packages = "\n\\usepackage[T1]{fontenc}\n\\usepackage{lmodern}"
    return re.sub(
        r"\\documentclass\s*(\[[^\]]*\])?\s*\{[^}]*\}",
        lambda match: match[0] + packages,
        source,
        count=1,
    )


def unescaped(text, characters):
    return len(re.findall(r"(?<!\\)[" + re.escape(characters) + "]", text))


def balanced(text):
    depth = 0
    for match in re.finditer(r"(?<!\\)[{}]", text):
        depth += 1 if match[0] == "{" else -1
        if depth < 0:
            return False
    return depth == 0


def cv_body(source):
    validate_cv(source)
    return source[source.index(BEGIN) + len(BEGIN) : source.rindex(END)]


def rewrite_cv(source, body):
    """Put the rewritten body into the user's CV, or raise what the model must fix."""
    original = cv_body(source)
    # Models often return the document markers too; anything outside them is not the body.
    body = body.split(BEGIN, 1)[-1].rsplit(END, 1)[0]
    if WHITESPACE.sub("", body) == WHITESPACE.sub("", original):
        raise DocumentError("The model returned the CV unchanged")
    added = set(COMMAND.findall(body)) - set(COMMAND.findall(original))
    if added:
        raise DocumentError(
            "The rewritten CV adds LaTeX commands that the original did not use",
            feedback="cv_body uses LaTeX the original does not: " + ", ".join(sorted(added)),
        )
    if not balanced(body):
        raise DocumentError("The rewritten CV has unbalanced braces")
    if unescaped(body, "%") > unescaped(original, "%"):
        raise DocumentError("The rewritten CV adds an unescaped % that would hide text")
    start = source.index(BEGIN) + len(BEGIN)
    return f"{source[:start]}\n{body.strip()}\n{source[source.rindex(END) :]}"


def diff_cv(before, after):
    """What actually changed, computed from the sources so it cannot omit anything.

    Compared word by word, so a moved line break alone is not reported as a change.
    Changes close together are merged, and each keeps a few words of context.
    """
    old, new = before.split(), after.split()
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [
        {
            "before": " ".join(old[group[0][1] : group[-1][2]]),
            "after": " ".join(new[group[0][3] : group[-1][4]]),
        }
        for group in matcher.get_grouped_opcodes(4)
    ]
