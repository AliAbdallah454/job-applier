"""Apply the model's edits to the user's LaTeX CV, build the letter and compile to PDF."""

import os
import shutil
import subprocess
from pathlib import Path

from backend.documents.errors import DocumentCompilationError, DocumentError
from backend.documents.latex import apply_edits, engine, escape
from backend.documents.models import GenerationInput, TailoredCv


def render_sources(request: GenerationInput, content: TailoredCv):
    profile = request.profile
    cv, _ = apply_edits(request.cv_latex, content.edits)
    contact = escape(" | ".join(filter(None, [profile.location, profile.email, profile.phone])))
    letter = "\n".join(
        [
            r"\documentclass[11pt,a4paper]{article}",
            r"\usepackage[margin=2.4cm]{geometry}",
            r"\usepackage{fontspec}",
            r"\setmainfont{DejaVu Sans}",
            r"\pagestyle{empty}",
            r"\setlength{\parindent}{0pt}",
            r"\setlength{\parskip}{10pt}",
            r"\begin{document}",
            r"\textbf{" + escape(profile.full_name) + r"}\par",
            contact + r"\par",
            r"\textbf{Application: " + escape(request.job.title) + r"}\par",
            escape(request.job.company or "Hiring team") + r"\par",
            "Dear Hiring Team,",
            *(escape(paragraph) + r"\par" for paragraph in content.letter_paragraphs),
            "Yours sincerely," + r"\par",
            escape(profile.full_name),
            r"\end{document}",
        ]
    )
    return {"cv.tex": cv.encode(), "motivation_letter.tex": letter.encode()}


def compile_pdf(source: Path, *, timeout=60):
    name = (
        engine(source.read_text(encoding="utf-8", errors="replace"))
        if source.exists()
        else "xelatex"
    )
    compiler = shutil.which(name)
    if not compiler:
        raise RuntimeError(f"{name} is required; use the document worker image or install TeX Live")

    # The CV source is the user's own LaTeX; model output inserted into it is always escaped.
    # Restrict file I/O and do not pass AWS credentials to the compiler process.
    try:
        subprocess.run(
            [
                compiler,
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
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
