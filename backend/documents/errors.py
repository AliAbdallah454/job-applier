"""Actionable diagnostics that exclude profiles, prompts and model response text."""

from collections import Counter

from pydantic import ValidationError


class DocumentError(ValueError):
    """An application-authored diagnostic; messages must never include private input.

    `feedback` is what the model is told on a retry. It may quote the CV or the model's
    output, so it goes back to the model only and is never logged or stored.
    """

    def __init__(self, message, *, feedback=None):
        super().__init__(message)
        self.feedback = feedback or message


class DocumentCompilationError(DocumentError):
    """Keep compiler output private while exposing a safe failure description."""

    def __init__(self, *, timed_out, diagnostics):
        reason = "timed out" if timed_out else "failed"
        log = diagnostics.decode("utf-8", "replace")
        first_error = max(log.find("\n!"), 0)
        super().__init__(
            f"XeLaTeX {reason}; download the compilation_log for details",
            feedback="LaTeX failed to compile:\n" + log[first_error:][:1500],
        )
        self.diagnostics = diagnostics


def error_detail(error):
    if isinstance(error, DocumentError):
        return str(error)
    if isinstance(error, ValidationError):
        counts = Counter(
            item["type"] for item in error.errors(include_input=False, include_url=False)
        )
        return "Schema validation failed: " + ", ".join(
            f"{kind}={count}" for kind, count in sorted(counts.items())
        )
    return type(error).__name__
