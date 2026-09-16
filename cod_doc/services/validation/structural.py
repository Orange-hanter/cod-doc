"""Structural validators — raise ValidationError on failure.

Called by services on write-path (TaskService.create, StoryService.create,
…) so a single source of truth gates every entry into the DB. Codes follow
the architecture's `<area>-NNN` scheme ([ARCHITECTURE.md §10]).
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from cod_doc.domain.entities import ScenarioKind, ScenarioStatus

from ._errors import ValidationError
from ._patterns import (
    _FORBIDDEN_TYPE_ALIASES,
    _ID_PREFIX_RE,
    _SCENARIO_COVERAGE_VERDICTS,
    _SCENARIO_GROUP_KEY_RE,
    _SCENARIO_ID_RE,
    _SECTION_SLUG_RE,
    _STORY_ID_RE,
    _TASK_ID_RE,
)


def validate_task_id(task_id: str) -> None:
    """`<PREFIX>-<NNN>` with optional sub-letter; PREFIX ∈ `[A-Z]{2,5}`.

    Per [task-plan.md §5, §8]. Examples: `AUTH-025`, `COD-011`, `AGN-021A`.
    """
    if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
        raise ValidationError(
            "TP-001",
            f"invalid task_id {task_id!r}: expected '<PREFIX>-NNN' "
            "with PREFIX of 2-5 capital letters and a 3-digit number "
            "(optional trailing capital for sub-tasks)",
            task_id=task_id,
        )


def validate_id_prefix(prefix: str) -> None:
    """`^[A-Z]{2,5}$` — used when auto-generating task IDs."""
    if not isinstance(prefix, str) or not _ID_PREFIX_RE.fullmatch(prefix):
        raise ValidationError(
            "TP-002",
            f"invalid id_prefix {prefix!r}: expected 2-5 capital letters",
            prefix=prefix,
        )


def validate_story_id(story_id: str) -> None:
    """`^[A-Z]{2,4}-\\d{3}$` — typically `US-NNN` per DATA_MODEL §6."""
    if not isinstance(story_id, str) or not _STORY_ID_RE.fullmatch(story_id):
        raise ValidationError(
            "US-001",
            f"invalid story_id {story_id!r}: expected '<PREFIX>-NNN' "
            "with PREFIX of 2-4 capital letters",
            story_id=story_id,
        )


def validate_section_slug(slug: str) -> None:
    """`^[A-Z]-<KebabSlug>$` — e.g. `A-Data-Core`, `B-Services`."""
    if not isinstance(slug, str) or not _SECTION_SLUG_RE.fullmatch(slug):
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


def validate_scenario_id(scenario_id: str) -> None:
    """`^SCN-\\d{3}$` — scenario ids are allocated max+1 within a project."""
    if not isinstance(scenario_id, str) or not _SCENARIO_ID_RE.fullmatch(scenario_id):
        raise ValidationError(
            "SCV-001",
            f"invalid scenario_id {scenario_id!r}: expected 'SCN-NNN'",
            scenario_id=scenario_id,
        )


def validate_scenario_kind(kind: str) -> None:
    """One of the five [RFC 24 §9] shapes; the vocabulary is not extended locally."""
    allowed = {k.value for k in ScenarioKind}
    if kind not in allowed:
        raise ValidationError(
            "SCV-002",
            f"invalid scenario kind {kind!r}: RFC 24 §9 defines exactly "
            f"{', '.join(sorted(allowed))}",
            kind=kind,
        )


def validate_scenario_status(status: str) -> None:
    """Claim status per [RFC 24 §8], never a §9 coverage verdict."""
    if status in _SCENARIO_COVERAGE_VERDICTS:
        raise ValidationError(
            "SCV-003",
            f"{status!r} is an RFC 24 §9 coverage verdict, not a claim status: "
            "coverage is evidence derived by the structure producer and lives in "
            "scenario_assessment (STR-002). Use draft | confirmed | retired.",
            status=status,
        )
    allowed = {s.value for s in ScenarioStatus}
    if status not in allowed:
        raise ValidationError(
            "SCV-003",
            f"invalid scenario status {status!r}: expected one of {', '.join(sorted(allowed))}",
            status=status,
        )


def validate_scenario_group_key(group_key: str) -> None:
    """`^[a-z0-9][a-z0-9-]{0,63}$` — the group key becomes a projection filename."""
    if not isinstance(group_key, str) or not _SCENARIO_GROUP_KEY_RE.fullmatch(group_key):
        raise ValidationError(
            "SCV-004",
            f"invalid scenario group_key {group_key!r}: expected lowercase "
            "letters, digits and dashes (max 64 chars, not starting with a dash)",
            group_key=group_key,
        )


def validate_scenario_body(*, preconditions: str, expected: str, steps: list[str]) -> None:
    """A scenario without preconditions, steps or an expected result is not one."""
    if not preconditions.strip():
        raise ValidationError("SCV-005", "scenario preconditions must not be empty")
    if not expected.strip():
        raise ValidationError("SCV-005", "scenario expected result must not be empty")
    if not steps or not any(step.strip() for step in steps):
        raise ValidationError("SCV-005", "scenario must have at least one step")
