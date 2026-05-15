"""LinkService — extract / resolve / verify outgoing links between sections.

COD-013. Implements [standards/document-link.md](../../docs/system/standards/document-link.md).

Pipeline per section:
1. `parse(body)` — pure: regex-extracts every link form into `ParsedLink`s.
2. `sync_section(section_id)` — replaces stored `link` rows for the section
   with the current parse output. Newly inserted rows are unresolved.
3. `resolve_section(section_id)` — runs sync if needed, then looks up each
   target in the DB; populates `to_doc_key` / `to_task_id` / `to_story_id`
   and the `resolved` flag.
4. `verify_section(section_id)` — re-runs the lookup against the current DB
   state and stamps `last_checked` / `broken_reason`. URL links are skipped
   (no network on write-paths — see standard §7).
5. `rename_cascade(old, new)` — when a document's `doc_key` changes,
   transactionally updates link rows AND rewrites canonical-ref bodies
   `[[doc:OLD]]` / `[[doc:OLD#a]]` → `[[doc:NEW…]]`. Each rewritten section
   gets a SECTION revision via DocService.

Caller owns the transaction.

Out of scope for COD-013 (deferred):
- Cross-project refs `[[doc:project:slug/key]]`.
- Fuzzy wiki resolution (Levenshtein) — only exact title/key match.
- HTTP reachability check for URL links.

COD-014a: markdown-relative path rewrite is now supported via the
optional `path_map` parameter to `rename_cascade`.
"""

from __future__ import annotations

from cod_doc.domain.entities import EntityKind  # re-exported for convenience

from ._types import (
    IncomingLink,
    LinkNotFoundError,
    ParsedLink,
    RenameCascadeReport,
    VerifyReport,
)
from .parser import parse
from .rename_cascade import rename_cascade
from .resolver import (
    list_code_refs,
    list_for_section,
    list_incoming_for_doc,
    resolve,
    resolve_section,
    sync_section,
    verify_section,
)

__all__ = [
    "EntityKind",
    "IncomingLink",
    "LinkNotFoundError",
    "ParsedLink",
    "RenameCascadeReport",
    "VerifyReport",
    "list_code_refs",
    "list_for_section",
    "list_incoming_for_doc",
    "parse",
    "rename_cascade",
    "resolve",
    "resolve_section",
    "sync_section",
    "verify_section",
]
