"""Advisory validators — return `list[ValidationIssue]` without raising.

Designed for the future `cod-doc audit` command and CI hooks; they cover
rules that are too strict / context-dependent to apply on every write
(verb-patterns, frontmatter freshness, owner-when-active, …). Surfaces
decide whether to escalate them to errors.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from cod_doc.domain.entities import DocumentStatus, DocumentType, TaskType

from ._errors import ValidationIssue
from ._patterns import _FM007_REQUIRED_TYPES, _VERB_PATTERNS


def audit_task_title(title: str, task_type: TaskType) -> list[ValidationIssue]:
    """Verb-pattern compliance per [task-plan.md §7].

    Recognized prefix → must match `task_type`. Unrecognized prefix → flag
    as warning (we don't know what it should map to).
    """
    if not isinstance(title, str) or not title.strip():
        return [ValidationIssue("TP-004", "error", "title is empty")]

    for pattern, expected in _VERB_PATTERNS:
        if pattern.match(title):
            if expected is task_type:
                return []
            return [
                ValidationIssue(
                    code="TP-004",
                    severity="error",
                    message=(
                        f"title verb-pattern matches type {expected.value!r} "
                        f"but task is declared as {task_type.value!r}"
                    ),
                    details={
                        "title": title,
                        "expected": expected.value,
                        "declared": task_type.value,
                    },
                )
            ]
    # No prefix matched.
    return [
        ValidationIssue(
            code="TP-004",
            severity="warning",
            message=(
                "title does not match any known verb-pattern "
                "(Test:, Test + Implement:, Implement:, Migration:, "
                "Refactor:, Fix:, Docs:)"
            ),
            details={"title": title},
        )
    ]


def audit_frontmatter(
    *,
    type: DocumentType | str,
    status: DocumentStatus | str,
    owner: str | None,
    source_of_truth: bool,
    frontmatter: dict[str, Any] | None = None,
    last_updated: datetime | None = None,
    now: datetime | None = None,
) -> list[ValidationIssue]:
    """Frontmatter rules per [frontmatter.md §6]."""
    issues: list[ValidationIssue] = []
    fm = frontmatter or {}
    status_str = status.value if isinstance(status, DocumentStatus) else status
    type_str = type.value if isinstance(type, DocumentType) else type

    # FM-002: status=active requires owner.
    if status_str == DocumentStatus.ACTIVE.value and not (owner and owner.strip()):
        issues.append(
            ValidationIssue(
                code="FM-002",
                severity="error",
                message="status=active requires a non-empty owner",
                details={"status": status_str},
            )
        )

    # FM-003: source_of_truth=false requires canonical_source.
    if not source_of_truth and not fm.get("canonical_source"):
        issues.append(
            ValidationIssue(
                code="FM-003",
                severity="error",
                message="source_of_truth=false requires a 'canonical_source' frontmatter field",
            )
        )

    # FM-004 / FM-005: last_updated freshness.
    if last_updated is not None:
        ref = now or datetime.now(UTC)
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=UTC)
        if last_updated > ref:
            issues.append(
                ValidationIssue(
                    code="FM-004",
                    severity="warning",
                    message="last_updated is in the future",
                    details={"last_updated": last_updated.isoformat()},
                )
            )
        elif status_str == DocumentStatus.ACTIVE.value and (ref - last_updated) > timedelta(
            days=180
        ):
            issues.append(
                ValidationIssue(
                    code="FM-005",
                    severity="warning",
                    message="active document is older than 180 days (stale-doc)",
                    details={
                        "last_updated": last_updated.isoformat(),
                        "age_days": (ref - last_updated).days,
                    },
                )
            )

    # FM-007: sensitivity field expected for spec/architecture/standard.
    if type_str in _FM007_REQUIRED_TYPES and "sensitivity" not in fm:
        issues.append(
            ValidationIssue(
                code="FM-007",
                severity="warning",
                message=(
                    f"document of type '{type_str}' should declare a 'sensitivity' "
                    "field (defaulting to 'internal')"
                ),
                details={"type": type_str},
            )
        )

    return issues


def audit_sensitivity(
    *,
    body: str,
    declared_sensitivity: str | None = None,
) -> list[ValidationIssue]:
    """Scan a document body for secrets / PII (advisory).

    Returns one `SD-001` issue per finding from `SensitivityScanner`. Public /
    internal documents with high-confidence findings get `severity=error`;
    confidential / restricted bodies get `severity=warning` (the content is
    expected to live behind a clearance gate, but secrets in plaintext still
    warrant a flag).
    """
    from cod_doc.services import sensitivity_scanner as _scanner

    issues: list[ValidationIssue] = []
    findings = _scanner.scan(body)
    if not findings:
        return issues
    high_clearance = (declared_sensitivity or "").lower() in {"confidential", "restricted"}
    for f in findings:
        is_high_conf_secret = f.kind != "pii_contact" and f.confidence >= 0.9
        severity = "warning" if (high_clearance or not is_high_conf_secret) else "error"
        issues.append(
            ValidationIssue(
                code="SD-001",
                severity=severity,
                message=f"sensitive content detected: {f.kind} (line {f.line})",
                details={
                    "kind": f.kind,
                    "line": f.line,
                    "snippet": f.snippet,
                    "confidence": f.confidence,
                },
            )
        )
    return issues
