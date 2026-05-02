"""Error & issue model for write-path validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ValidationError(ValueError):
    """Write-path validation failure with a stable `code` for surface routing.

    Mirrors the future `cod_doc.errors.ValidationError` from
    [ARCHITECTURE.md §10]; we declare it here to avoid a project-wide
    refactor in this task. When the global error module lands, this can
    re-export from there.
    """

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(slots=True)
class ValidationIssue:
    """Advisory finding produced by `audit_*` validators.

    `severity` ∈ {"error", "warning", "info"}. Surfaces decide whether to
    escalate. `code` matches the same scheme as `ValidationError.code`.
    """

    code: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
