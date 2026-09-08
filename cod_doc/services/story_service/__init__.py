"""StoryService — write/read paths for user stories.

COD-014. Implements [capability/user-stories-graph.md].

Public API:
- `create` — story + optional initial acceptance criteria; STORY revision.
- `get` / `list_for_project` / `list_acceptance` / `list_links` / `list_tasks`.
- `update_status` — set story.status; supports optimistic concurrency.
- `add_criterion` / `set_criterion_met` — manage acceptance criteria.
- `create_section` / `list_sections` / `get_section` / `assign_section` —
  реестр продуктовых модулей и привязка истории к секции (ADO-143).
- `link` — attach story to task / document / module with a relation kind;
  hard-errors on broken refs and de-dupes existing edges.
- `coverage` — derived `CoverageStatus` from acceptance + linked task progress.

All mutations use `entity_kind=STORY` for revisions; diffs are JSON-patch
fragments with an `op` discriminator.

Caller owns the transaction.
"""

from __future__ import annotations

from ._types import (
    AcceptanceNotFoundError,
    BrokenLinkError,
    CoverageStatus,
    SectionAlreadyExistsError,
    SectionNotFoundError,
    StoryAlreadyExistsError,
    StoryCoverage,
    StoryNotFoundError,
)
from .acceptance import add_criterion, set_criterion_met
from .coverage import coverage
from .crud import (
    create,
    get,
    list_acceptance,
    list_for_project,
    list_links,
    list_tasks,
    next_story_id,
    update_status,
)
from .links import link
from .sections import assign_section, create_section, get_section, list_sections

__all__ = [
    "AcceptanceNotFoundError",
    "BrokenLinkError",
    "CoverageStatus",
    "SectionAlreadyExistsError",
    "SectionNotFoundError",
    "StoryAlreadyExistsError",
    "StoryCoverage",
    "StoryNotFoundError",
    "add_criterion",
    "assign_section",
    "coverage",
    "create",
    "create_section",
    "get",
    "get_section",
    "link",
    "list_acceptance",
    "list_for_project",
    "list_links",
    "list_sections",
    "list_tasks",
    "next_story_id",
    "set_criterion_met",
    "update_status",
]
