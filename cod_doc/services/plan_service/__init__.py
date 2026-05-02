"""PlanService — read-paths over plans + projection export + graph queries.

COD-012/COD-021. Pure read-side service: no mutations, no revisions.

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

Caller owns the transaction.
"""

from __future__ import annotations

from ._types import (
    ChainEntry,
    CriticalPathResult,
    DerivedStatus,
    PlanAuditReport,
    PlanNotFoundError,
    PlanProgress,
    SectionProgress,
    TaskNotFoundInPlanError,
)
from .audit import audit
from .export import export
from .graph import critical_path, forward_chain, reverse_chain
from .reads import get_for_project, list_for_project, ready, recalc

__all__ = [
    "ChainEntry",
    "CriticalPathResult",
    "DerivedStatus",
    "PlanAuditReport",
    "PlanNotFoundError",
    "PlanProgress",
    "SectionProgress",
    "TaskNotFoundInPlanError",
    "audit",
    "critical_path",
    "export",
    "forward_chain",
    "get_for_project",
    "list_for_project",
    "ready",
    "recalc",
    "reverse_chain",
]
