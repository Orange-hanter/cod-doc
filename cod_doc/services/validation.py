"""Centralized write-path validation.

COD-020. Implements rules from
[standards/task-plan.md](../../docs/system/standards/task-plan.md)
and [standards/frontmatter.md](../../docs/system/standards/frontmatter.md).

Two flavours of validators:

- **Structural** — `validate_*` functions that raise `ValidationError` on
  failure. Called by services on write-path (TaskService.create,
  StoryService.create, …) so a single source of truth gates every entry into
  the DB. Codes follow the architecture's `<area>-NNN` scheme
  ([ARCHITECTURE.md §10](../../docs/system/ARCHITECTURE.md)).

- **Advisory** — `audit_*` functions that return `list[ValidationIssue]`
  without raising. Designed for the future `cod-doc audit` command and CI
  hooks; they cover rules that are too strict / context-dependent to apply
  on every write (verb-patterns, frontmatter freshness, owner-when-active,
  …). Surfaces decide whether to escalate them to errors.

Codes:
- `TP-001` — task_id format (`^[A-Z]{2,5}-\\d{3}[A-Z]?$`)
- `TP-002` — id_prefix format (`^[A-Z]{2,5}$`)
- `TP-003` — section slug format (`^[A-Z]-[A-Za-z0-9-]+$`)
- `TP-004` — title verb-pattern mismatch (advisory)
- `TP-005` — forbidden task type alias
- `US-001` — story_id format (`^[A-Z]{2,4}-\\d{3}$`)
- `FM-001` — type missing / unknown (handled by domain enum upstream)
- `FM-002` — `status=active` with empty `owner`
- `FM-003` — `source_of_truth=false` without `canonical_source`
- `FM-004` — `last_updated` is in the future
- `FM-005` — `last_updated` older than 180 days for `status=active`
- `SD-100` — document path is absolute, contains `..`, or escapes the project root
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    TaskType,
)

# --------------------------------------------------------------------------- #
# Error model                                                                   #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Compiled patterns                                                             #
# --------------------------------------------------------------------------- #


_TASK_ID_RE = re.compile(r"^[A-Z]{2,5}-\d{3}[A-Z]?$")
_ID_PREFIX_RE = re.compile(r"^[A-Z]{2,5}$")
_STORY_ID_RE = re.compile(r"^[A-Z]{2,4}-\d{3}$")
_SECTION_SLUG_RE = re.compile(r"^[A-Z]-[A-Za-z0-9][A-Za-z0-9-]*$")

# Verb-patterns from [task-plan.md §7]. Maps a regex prefix to the type it
# implies. Order matters: the more-specific pattern (`Test + Implement`)
# comes before the looser one (`Test:`).
_VERB_PATTERNS: list[tuple[re.Pattern[str], TaskType]] = [
    (re.compile(r"^Test \+ Implement:\s+\S"), TaskType.FEATURE),
    (re.compile(r"^Implement:\s+\S"), TaskType.FEATURE),
    (re.compile(r"^Test:\s+\S"), TaskType.TEST),
    (re.compile(r"^Migration:\s+\S"), TaskType.MIGRATION),
    (re.compile(r"^Refactor:\s+\S"), TaskType.REFACTOR),
    (re.compile(r"^Fix:\s+\S"), TaskType.BUG),
    (re.compile(r"^Docs:\s+\S"), TaskType.DOCS),
]

# Forbidden type aliases per [task-plan.md §6].
_FORBIDDEN_TYPE_ALIASES = {"implementation", "migration+feature"}


# --------------------------------------------------------------------------- #
# Structural validators (raise ValidationError)                                #
# --------------------------------------------------------------------------- #


def validate_task_id(task_id: str) -> None:
    """`<PREFIX>-<NNN>` with optional sub-letter; PREFIX ∈ `[A-Z]{2,5}`.

    Per [task-plan.md §5, §8]. Examples: `AUTH-025`, `COD-011`, `AGN-021A`.
    """
    if not isinstance(task_id, str) or not _TASK_ID_RE.match(task_id):
        raise ValidationError(
            "TP-001",
            f"invalid task_id {task_id!r}: expected '<PREFIX>-NNN' "
            "with PREFIX of 2-5 capital letters and a 3-digit number "
            "(optional trailing capital for sub-tasks)",
            task_id=task_id,
        )


def validate_id_prefix(prefix: str) -> None:
    """`^[A-Z]{2,5}$` — used when auto-generating task IDs."""
    if not isinstance(prefix, str) or not _ID_PREFIX_RE.match(prefix):
        raise ValidationError(
            "TP-002",
            f"invalid id_prefix {prefix!r}: expected 2-5 capital letters",
            prefix=prefix,
        )


def validate_story_id(story_id: str) -> None:
    """`^[A-Z]{2,4}-\\d{3}$` — typically `US-NNN` per DATA_MODEL §6."""
    if not isinstance(story_id, str) or not _STORY_ID_RE.match(story_id):
        raise ValidationError(
            "US-001",
            f"invalid story_id {story_id!r}: expected '<PREFIX>-NNN' "
            "with PREFIX of 2-4 capital letters",
            story_id=story_id,
        )


def validate_section_slug(slug: str) -> None:
    """`^[A-Z]-<KebabSlug>$` — e.g. `A-Data-Core`, `B-Services`."""
    if not isinstance(slug, str) or not _SECTION_SLUG_RE.match(slug):
        raise ValidationError(
            "TP-003",
            f"invalid section slug {slug!r}: expected '<LETTER>-<KebabSlug>'",
            slug=slug,
        )


def validate_task_type(type_value: str) -> None:
    """Reject forbidden aliases (`implementation`, etc.). Domain enum
    catches the unknown-value case upstream — this is the second line for
    spec-listed bans."""
    if type_value in _FORBIDDEN_TYPE_ALIASES:
        raise ValidationError(
            "TP-005",
            f"forbidden task type alias {type_value!r}: "
            "use 'feature' instead of 'implementation'; "
            "split compound types into separate tasks",
            type=type_value,
        )


def validate_doc_path(path: str) -> None:
    """Reject document paths that could escape the project root.

    Document paths are stored verbatim and combined with the project's
    `root_path` by `projection_service.export_document` via `root_path / path`.
    Pathlib's `/` operator returns the right operand when it is absolute, so
    an absolute or `..`-bearing path lets a caller break the containment
    invariant and write outside the project tree. Rejected forms:
      * empty / whitespace-only
      * absolute (POSIX `/etc/foo` OR Windows `C:\\foo` — both checked
        regardless of host OS, since the DB is portable)
      * any segment equal to `..`
    """
    if not isinstance(path, str) or not path.strip():
        raise ValidationError(
            "SD-100",
            "document path is empty",
            path=path,
        )
    posix = PurePosixPath(path)
    if posix.is_absolute() or PureWindowsPath(path).is_absolute():
        raise ValidationError(
            "SD-100",
            f"document path must be relative, got {path!r}",
            path=path,
        )
    if any(part == ".." for part in posix.parts):
        raise ValidationError(
            "SD-100",
            f"document path must not contain '..' segments, got {path!r}",
            path=path,
        )


# --------------------------------------------------------------------------- #
# Advisory validators (return ValidationIssue list)                             #
# --------------------------------------------------------------------------- #


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
                    code="TP-004", severity="error",
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
            code="TP-004", severity="warning",
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

    # FM-002: status=active requires owner.
    if status_str == DocumentStatus.ACTIVE.value and not (owner and owner.strip()):
        issues.append(
            ValidationIssue(
                code="FM-002", severity="error",
                message="status=active requires a non-empty owner",
                details={"status": status_str},
            )
        )

    # FM-003: source_of_truth=false requires canonical_source.
    if not source_of_truth and not fm.get("canonical_source"):
        issues.append(
            ValidationIssue(
                code="FM-003", severity="error",
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
                    code="FM-004", severity="warning",
                    message="last_updated is in the future",
                    details={"last_updated": last_updated.isoformat()},
                )
            )
        elif (
            status_str == DocumentStatus.ACTIVE.value
            and (ref - last_updated) > timedelta(days=180)
        ):
            issues.append(
                ValidationIssue(
                    code="FM-005", severity="warning",
                    message="active document is older than 180 days (stale-doc)",
                    details={
                        "last_updated": last_updated.isoformat(),
                        "age_days": (ref - last_updated).days,
                    },
                )
            )

    return issues


__all__ = [
    "ValidationError",
    "ValidationIssue",
    "audit_frontmatter",
    "audit_task_title",
    "validate_id_prefix",
    "validate_section_slug",
    "validate_story_id",
    "validate_task_id",
    "validate_task_type",
]
