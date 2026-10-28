"""Actionable diagnostics that exclude profiles, prompts and model response text."""

from collections import Counter

from pydantic import ValidationError


class DocumentError(ValueError):
    """An application-authored diagnostic; messages must never include private input."""


class DocumentCompilationError(DocumentError):
    """Keep compiler output private while exposing a safe failure description."""

    def __init__(self, *, timed_out, diagnostics):
        reason = "timed out" if timed_out else "failed"
        super().__init__(f"XeLaTeX {reason}; download the compilation_log for details")
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
