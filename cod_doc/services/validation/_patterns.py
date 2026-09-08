"""Compiled regex patterns and lookup tables used by validators."""

from __future__ import annotations

import re

from cod_doc.domain.entities import DocumentType, TaskType

# ADO-021: these are applied with `.fullmatch()`, not `.match()`. In Python `$`
# also matches just before a trailing newline, so `.match()` accepted `'AA\n'`
# as an id_prefix and let it through into generated task IDs.
_TASK_ID_RE = re.compile(r"^[A-Z]{2,5}-\d{3}[A-Z]?$")
_ID_PREFIX_RE = re.compile(r"^[A-Z]{2,5}$")
_STORY_ID_RE = re.compile(r"^[A-Z]{2,4}-\d{3}$")
_SECTION_SLUG_RE = re.compile(r"^[A-Z]-[A-Za-z0-9][A-Za-z0-9-]*$")
# ADO-143: ключ секции историй уходит в путь роута и в CSS-селектор htmx,
# поэтому строго [a-z0-9-] — ни точек, ни слэшей, ни пробелов.
_STORY_SECTION_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")

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

_FM007_REQUIRED_TYPES = frozenset(
    {
        DocumentType.MODULE_SPEC.value,
        DocumentType.ARCHITECTURE.value,
        DocumentType.STANDARD.value,
    }
)
