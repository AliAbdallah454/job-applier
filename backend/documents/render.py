"""Put the rewritten body into the user's LaTeX CV, build the letters and compile to PDF."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from backend.documents.errors import DocumentCompilationError, DocumentError
from backend.documents.latex import engine, escape, rewrite_cv, with_accented_text
from backend.documents.models import GenerationInput, TailoredCv

LETTER_WORDING = {
    "en": {
        "application": "Application: ",
        "recipient": "Hiring team",
        "greeting": "Dear Hiring Team,",
        "closing": "Yours sincerely,",
        "layout": "",
    },
    "fr": {
        "application": "Candidature : ",
        "recipient": "Équipe de recrutement",
        "greeting": "Madame, Monsieur,",
        "closing": "Je vous prie d'agréer, Madame, Monsieur, "
        "l'expression de mes salutations distinguées.",
        # The worker has no French hyphenation patterns, and English ones split words wrongly.
        "layout": r"\raggedright\hyphenpenalty=10000",
    },
}


def render_letter(request: GenerationInput, paragraphs):
    profile = request.profile
    wording = LETTER_WORDING[request.language]
    contact = escape(" | ".join(filter(None, [profile.location, profile.email, profile.phone])))
    return "\n".join(
        [
            r"\documentclass[11pt,a4paper]{article}",
            r"\usepackage[margin=2.4cm]{geometry}",
            r"\usepackage{fontspec}",
            # A user's edit compiles where the API runs, and a Mac does not have DejaVu.
            r"\IfFontExistsTF{DejaVu Sans}{\setmainfont{DejaVu Sans}}{\setmainfont{Helvetica}}",
            r"\pagestyle{empty}",
            r"\setlength{\parindent}{0pt}",
            r"\setlength{\parskip}{10pt}",
            r"\begin{document}",
            wording["layout"],
            r"\textbf{" + escape(profile.full_name) + r"}\par",
            contact + r"\par",
            r"\textbf{" + wording["application"] + escape(request.job.title) + r"}\par",
            escape(request.job.company or wording["recipient"]) + r"\par",
            wording["greeting"],
            *(escape(paragraph) + r"\par" for paragraph in paragraphs),
            wording["closing"] + r"\par",
            escape(profile.full_name),
            r"\end{document}",
        ]
    )


def render_sources(request: GenerationInput, content: TailoredCv):
    cv = rewrite_cv(request.cv_latex, content.cv_body)
    return {
        "cv.tex": (with_accented_text(cv) if request.language == "fr" else cv).encode(),
        "motivation_letter.tex": render_letter(
            request, content.motivation_letter_paragraphs
        ).encode(),
        "cover_letter.tex": render_letter(request, content.cover_letter_paragraphs).encode(),
    }


def read_synctex(source: Path):
    """The file linking the PDF back to source lines, if this compiler wrote one."""
    output = source.with_suffix(".synctex.gz")
    return output.read_bytes() if output.exists() else None


def compile_source(source: str, compile_document):
    with tempfile.TemporaryDirectory(prefix="job-documents-") as directory:
        path = Path(directory) / "cv.tex"
        path.write_text(source, encoding="utf-8")
        return compile_document(path), read_synctex(path)


def source_line(synctex: bytes, page: int, x: float, y: float, *, timeout=10):
    """The cv.tex line at a PDF position in points from the page's top left, or None."""
    tool = shutil.which("synctex")
    if not tool:
        raise RuntimeError("synctex is required; install TeX Live")
    with tempfile.TemporaryDirectory(prefix="job-documents-") as directory:
        (Path(directory) / "cv.synctex.gz").write_bytes(synctex)
        try:
            result = subprocess.run(
                [tool, "edit", "-o", f"{page}:{x:.2f}:{y:.2f}:cv.pdf"],
                cwd=directory,
                check=True,
                capture_output=True,
                timeout=timeout,
                env={"PATH": os.environ.get("PATH", ""), "HOME": directory, "LANG": "C.UTF-8"},
            )
        except subprocess.SubprocessError as exc:
            raise RuntimeError("synctex could not read this CV's position data") from exc
    # Package files can also appear in the results, and the user can only edit cv.tex.
    current = ""
    for row in result.stdout.decode("utf-8", "replace").splitlines():
        if row.startswith("Input:"):
            current = row.removeprefix("Input:")
        elif row.startswith("Line:") and PurePosixPath(current).name == "cv.tex":
            return int(row.removeprefix("Line:"))
    return None


def compile_pdf(source: Path, *, timeout=60):
    name = (
        engine(source.read_text(encoding="utf-8", errors="replace"))
        if source.exists()
        else "xelatex"
    )
    compiler = shutil.which(name)
    if not compiler:
        raise RuntimeError(f"{name} is required; use the document worker image or install TeX Live")

    # The CV is the user's LaTeX with a checked model rewrite of its body; letters are escaped.
    # Restrict file I/O and do not pass AWS credentials to the compiler process.
    try:
        subprocess.run(
            [
                compiler,
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-synctex=1",
                source.name,
            ],
            cwd=source.parent,
            check=True,
            capture_output=True,
            timeout=timeout,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(source.parent),
                "LANG": "C.UTF-8",
                "openin_any": "p",
                "openout_any": "p",
            },
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log = source.with_suffix(".log")
        diagnostics = log.read_bytes() if log.exists() else b""
        for output in (exc.stdout, exc.stderr):
            if output:
                diagnostics += b"\n" + (output.encode() if isinstance(output, str) else output)
        raise DocumentCompilationError(
            timed_out=isinstance(exc, subprocess.TimeoutExpired),
            diagnostics=diagnostics[-2_000_000:],
        ) from exc
    pdf = source.with_suffix(".pdf").read_bytes()
    if not pdf.startswith(b"%PDF-"):
        raise DocumentError("LaTeX did not produce a valid PDF file")
    return pdf
