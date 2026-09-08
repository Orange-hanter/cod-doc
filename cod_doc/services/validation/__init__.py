"""Centralized write-path validation.

COD-020. Implements rules from
[standards/task-plan.md](../../docs/system/standards/task-plan.md)
and [standards/frontmatter.md](../../docs/system/standards/frontmatter.md).

Two flavours of validators:

- **Structural** — `validate_*` functions that raise `ValidationError` on
  failure. Called by services on write-path so a single source of truth
  gates every entry into the DB.

- **Advisory** — `audit_*` functions that return `list[ValidationIssue]`
  without raising. Designed for the future `cod-doc audit` command and CI
  hooks; surfaces decide whether to escalate them to errors.

Codes:
- `TP-001` — task_id format (`^[A-Z]{2,5}-\\d{3}[A-Z]?$`)
- `TP-002` — id_prefix format (`^[A-Z]{2,5}$`)
- `TP-003` — section slug format (`^[A-Z]-[A-Za-z0-9-]+$`)
- `TP-004` — title verb-pattern mismatch (advisory)
- `TP-005` — forbidden task type alias
- `US-001` — story_id format (`^[A-Z]{2,4}-\\d{3}$`)
- `SCV-001` — scenario_id format (`^SCN-\\d{3}$`)
- `SCV-002` — scenario kind outside the RFC 24 §9 vocabulary
- `SCV-003` — scenario status: a §9 coverage verdict used as a §8 claim status
- `SCV-004` — scenario group_key format (`^[a-z0-9][a-z0-9-]{0,63}$`)
- `SCV-005` — scenario body: empty preconditions / expected / steps
- `FM-002` — `status=active` with empty `owner`
- `FM-003` — `source_of_truth=false` without `canonical_source`
- `FM-004` — `last_updated` is in the future
- `FM-005` — `last_updated` older than 180 days for `status=active`
- `FM-007` — `sensitivity` field missing for type ∈ {module-spec, architecture, standard}
- `TY-001` — documents still on import fallback (`module-spec`+`draft`, no authored `type:`)
- `SD-001` — sensitive content (secret pattern / PII) detected in document body
- `SD-100` — document path is absolute, contains `..`, or escapes the project root
"""

from __future__ import annotations

from ._errors import ValidationError, ValidationIssue
from .advisory import (
    audit_frontmatter,
    audit_import_fallback,
    audit_sensitivity,
    audit_task_title,
    is_import_fallback,
)
from .structural import (
    validate_doc_path,
    validate_id_prefix,
    validate_scenario_body,
    validate_scenario_group_key,
    validate_scenario_id,
    validate_scenario_kind,
    validate_scenario_status,
    validate_section_slug,
    validate_story_id,
    validate_task_id,
    validate_task_type,
)

__all__ = [
    "ValidationError",
    "ValidationIssue",
    "audit_frontmatter",
    "audit_import_fallback",
    "audit_sensitivity",
    "audit_task_title",
    "is_import_fallback",
    "validate_doc_path",
    "validate_id_prefix",
    "validate_scenario_body",
    "validate_scenario_group_key",
    "validate_scenario_id",
    "validate_scenario_kind",
    "validate_scenario_status",
    "validate_section_slug",
    "validate_story_id",
    "validate_task_id",
    "validate_task_type",
]
