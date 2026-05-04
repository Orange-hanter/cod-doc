"""StoryService — write/read paths for user stories.

COD-014. Implements [capability/user-stories-graph.md].

Public API:
- `create` — story + optional initial acceptance criteria; STORY revision.
- `get` / `list_for_project` / `list_acceptance` / `list_links` / `list_tasks`.
- `update_status` — set story.status; supports optimistic concurrency.
- `add_criterion` / `set_criterion_met` — manage acceptance criteria.
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

__all__ = [
    "AcceptanceNotFoundError",
    "BrokenLinkError",
    "CoverageStatus",
    "StoryAlreadyExistsError",
    "StoryCoverage",
    "StoryNotFoundError",
    "add_criterion",
    "coverage",
    "create",
    "get",
    "link",
    "list_acceptance",
    "list_for_project",
    "list_links",
    "list_tasks",
    "next_story_id",
    "set_criterion_met",
    "update_status",
]
