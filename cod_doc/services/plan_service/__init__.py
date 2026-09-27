"""PlanService — read-paths over plans + projection export + graph queries.

COD-012/COD-021. The package holds the read paths and, in ``sections.py``,
the single write path for plans and plan sections: every mutation there
writes a revision and an activity event in the caller's transaction
(ADO-040, RFC 26 §3.1).

Public API (COD-012):
- `recalc` — derived status for the plan and each of its sections.
- `ready` — `ready_tasks` view filtered by plan, priority-ordered.
- `audit` — integrity checks: cycle detection + done-drift.
- `export` — markdown projections (Progress Overview / Next Batch / Mermaid).

Graph API (COD-021 — per [user-stories-graph.md §6]):
- `forward_chain(task_id)` — prerequisites of task_id (what must complete
  BEFORE it), via recursive CTE following blocks-edges.
- `reverse_chain(task_id)` — dependents of task_id (tasks unblocked AFTER it
  completes), via recursive CTE.
- `critical_path(plan_id)` — longest sequential chain in the plan
  (SQL CTE for depth computation + Python backtrack for path reconstruction).

Write API (ADO-201, RFC 26 §3.1 — ``sections.py``):
- `create_plan` — new plan; scope is unique per DB.
- `create_section` — new plan section; validated by `build_section`.
- `update_section` — change title/slug/doc_id; idempotent, `adopt=True`.
- `move_section` — reorder sections into a dense 0..n-1.
- `delete_section` — remove a section; non-empty only with `reassign_to`.

Caller owns the transaction.
"""

from __future__ import annotations

from ._internals import build_section
from ._types import (
    ChainEntry,
    CriticalPathResult,
    DerivedStatus,
    PlanAuditReport,
    PlanNotFoundError,
    PlanProgress,
    ReadyBatch,
    SectionProgress,
    TaskNotFoundInPlanError,
)
from .audit import audit
from .export import export, freeze_projection
from .graph import chain_layout, critical_path, forward_chain, reverse_chain
from .reads import (
    get_by_scope,
    get_for_project,
    list_for_project,
    list_sections,
    ready,
    ready_batch,
    ready_batch_for_project,
    ready_for_project,
    recalc,
    recalc_for_project,
)
from .sections import (
    PlanAlreadyExistsError,
    SectionAlreadyExistsError,
    SectionHasTasksError,
    SectionNotFoundError,
    create_plan,
    create_section,
    delete_section,
    move_section,
    update_section,
)

__all__ = [
    "ChainEntry",
    "CriticalPathResult",
    "DerivedStatus",
    "PlanAlreadyExistsError",
    "PlanAuditReport",
    "PlanNotFoundError",
    "PlanProgress",
    "ReadyBatch",
    "SectionAlreadyExistsError",
    "SectionHasTasksError",
    "SectionNotFoundError",
    "SectionProgress",
    "TaskNotFoundInPlanError",
    "audit",
    "build_section",
    "chain_layout",
    "create_plan",
    "create_section",
    "critical_path",
    "delete_section",
    "export",
    "forward_chain",
    "freeze_projection",
    "get_by_scope",
    "get_for_project",
    "list_for_project",
    "list_sections",
    "move_section",
    "ready",
    "ready_batch",
    "ready_batch_for_project",
    "ready_for_project",
    "recalc",
    "recalc_for_project",
    "reverse_chain",
    "update_section",
]
