"""Insert escaped content into trusted LaTeX templates and compile with XeLaTeX."""

import os
import re
import shutil
import subprocess
from pathlib import Path

from backend.documents.errors import DocumentCompilationError, DocumentError
from backend.documents.models import GeneratedContent, GenerationInput

DEFAULT_TEMPLATE = Path(__file__).parent / "templates" / "cv.tex"
TOKENS = {"NAME", "CONTACT", "HEADLINE", "SUMMARY", "SECTIONS", "RESUME_SECTIONS", "RESUME_CONTACT"}


def validate_template(template):
    placeholders = set(re.findall(r"<<([A-Z_]+)>>", template))
    if (
        "NAME" not in placeholders
        or not {"SECTIONS", "RESUME_SECTIONS"} & placeholders
        or not placeholders <= TOKENS
    ):
        raise DocumentError(
            "CV template requires <<NAME>> and <<SECTIONS>> or <<RESUME_SECTIONS>>; "
            "unsupported placeholder"
        )
    if "\\documentclass" not in template or "\\end{document}" not in template:
        raise DocumentError("Provide a complete self-contained LaTeX document")
    return template


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


def dates(entry):
    return " – ".join(
        filter(None, [entry.start_date, "Present" if entry.current else entry.end_date])
    )


def resume_contact(profile):
    parts = [escape(value) for value in (profile.location, profile.phone) if value]
    if profile.email:
        parts.append(
            r"\href{mailto:"
            + escape(profile.email)
            + r"}{\underline{"
            + escape(profile.email)
            + "}}"
        )
    for link in profile.links:
        parts.append(r"\href{" + escape(link.url) + r"}{\underline{" + escape(link.label) + "}}")
    return r" $|$ ".join(parts)


def resume_sections(profile, content):
    """Use the supplied resume macros, with education first and factual headings."""
    sections = []

    def heading(name):
        sections.extend([r"\section{" + name + "}", r"\resumeSubHeadingListStart"])

    def command(name, *values):
        return "\\" + name + "".join("{" + escape(value or "") + "}" for value in values)

    def bullets(items):
        sections.append(r"\resumeItemListStart")
        sections.extend(command("resumeItem", item) for item in items)
        sections.append(r"\resumeItemListEnd")

    education = {item.id: item for item in profile.education}
    if content.education_ids:
        heading("Education")
        for source_id in content.education_ids:
            entry = education[source_id]
            qualification = entry.qualification
            if entry.field_of_study:
                qualification += ", " + entry.field_of_study
            sections.append(
                command("resumeSubheading", entry.institution, "", qualification, dates(entry))
            )
        sections.append(r"\resumeSubHeadingListEnd")

    experiences = {item.id: item for item in profile.experiences}
    if content.experiences:
        heading("Experience")
        for selection in content.experiences:
            entry = experiences[selection.source_id]
            sections.append(
                command(
                    "resumeSubheading", entry.role, dates(entry), entry.organization, entry.location
                )
            )
            bullets(selection.bullets)
        sections.append(r"\resumeSubHeadingListEnd")

    projects = {item.id: item for item in profile.projects}
    if content.projects:
        heading("Projects")
        for selection in content.projects:
            entry = projects[selection.source_id]
            title = r"\textbf{" + escape(entry.name) + "}"
            if entry.technologies:
                title += r" $|$ \emph{" + escape(", ".join(entry.technologies)) + "}"
            sections.append(r"\resumeProjectHeading{" + title + "}{" + escape(dates(entry)) + "}")
            bullets(selection.bullets)
        sections.append(r"\resumeSubHeadingListEnd")

    certifications = {item.id: item for item in profile.certifications}
    for name, items in (
        ("Technical Skills", [", ".join(content.skill_names)] if content.skill_names else []),
        (
            "Certificates",
            [
                certifications[key].name + " — " + certifications[key].issuer
                for key in content.certification_ids
            ],
        ),
        (
            "Languages",
            [
                item.name + (": " + item.proficiency if item.proficiency else "")
                for item in profile.languages
            ],
        ),
    ):
        if items:
            sections.extend(
                [
                    r"\section{" + name + "}",
                    r"\begin{itemize}[leftmargin=0.22in, itemsep=0pt, parsep=0pt, topsep=0pt]",
                ]
            )
            sections.extend(r"\item \small{" + escape(item) + "}" for item in items)
            sections.append(r"\end{itemize}")

    return "\n".join(sections)


def render_sources(request: GenerationInput, content: GeneratedContent):
    profile = request.profile
    content.validate_sources(profile)
    validate_template(request.cv_template)
    sections = []

    for heading, selected, entries, title in (
        (
            "Experience",
            content.experiences,
            profile.experiences,
            lambda item: f"{item.role} — {item.organization}",
        ),
        ("Projects", content.projects, profile.projects, lambda item: item.name),
    ):
        if selected:
            sections.append(r"\section*{" + heading + "}")
        indexed = {entry.id: entry for entry in entries}
        for selection in selected:
            entry = indexed[selection.source_id]
            sections.append(r"\textbf{" + escape(title(entry)) + "} " + escape(dates(entry)))
            sections.append(r"\begin{itemize}")
            sections.extend(r"\item " + escape(bullet) for bullet in selection.bullets)
            sections.append(r"\end{itemize}")

    for heading, selected, entries, describe in (
        (
            "Education",
            content.education_ids,
            profile.education,
            lambda item: (
                f"{item.qualification}, {item.institution}"
                + (f" — {item.field_of_study}" if item.field_of_study else "")
                + f" ({dates(item)})"
            ),
        ),
        (
            "Certifications",
            content.certification_ids,
            profile.certifications,
            lambda item: (
                f"{item.name} — {item.issuer}" + (f" ({item.issued_at})" if item.issued_at else "")
            ),
        ),
    ):
        if selected:
            sections.append(r"\section*{" + heading + "}")
        indexed = {entry.id: entry for entry in entries}
        sections.extend(escape(describe(indexed[id])) + r"\par" for id in selected)

    if content.skill_names:
        sections.extend([r"\section*{Skills}", escape(", ".join(content.skill_names))])
    if profile.languages:
        sections.extend(
            [
                r"\section*{Languages}",
                escape(
                    "; ".join(
                        language.name
                        + (f" ({language.proficiency})" if language.proficiency else "")
                        for language in profile.languages
                    )
                ),
            ]
        )

    values = {
        "NAME": escape(profile.full_name),
        "CONTACT": escape(
            " | ".join(filter(None, [profile.location, profile.email, profile.phone]))
        ),
        "HEADLINE": escape(content.headline),
        "SUMMARY": escape(content.summary),
        "SECTIONS": "\n".join(sections),
    }
    if "<<RESUME_SECTIONS>>" in request.cv_template:
        values["RESUME_SECTIONS"] = resume_sections(profile, content)
    if "<<RESUME_CONTACT>>" in request.cv_template:
        values["RESUME_CONTACT"] = resume_contact(profile)
    cv = re.sub(r"<<([A-Z_]+)>>", lambda match: values[match[1]], request.cv_template)
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
            values["CONTACT"] + r"\par",
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
    compiler = shutil.which("xelatex")
    if not compiler:
        raise RuntimeError("XeLaTeX is required; use the document worker image or install TeX Live")

    # Templates are trusted application/user assets; model output is always escaped.
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
