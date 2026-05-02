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
  Returns `CriticalPathResult` with ordered `task_ids` and `chain` entries.

Caller owns the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from cod_doc.domain.entities import Priority, Task, TaskStatus
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    TaskModel,
)
from cod_doc.infra.repositories import TaskRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PlanNotFoundError(LookupError):
    pass


class TaskNotFoundInPlanError(LookupError):
    pass


class DerivedStatus(StrEnum):
    EMPTY = "empty"
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    DONE = "done"


_PRIORITY_ORDER: dict[str, int] = {
    Priority.CRITICAL.value: 0,
    Priority.HIGH.value: 1,
    Priority.MEDIUM.value: 2,
    Priority.LOW.value: 3,
}


@dataclass(slots=True)
class SectionProgress:
    section_id: int
    letter: str
    title: str
    slug: str
    position: int
    total: int
    done: int
    in_progress: int
    status: DerivedStatus

    @property
    def remaining(self) -> int:
        return self.total - self.done


@dataclass(slots=True)
class PlanProgress:
    plan_id: int
    scope: str
    total: int
    done: int
    in_progress: int
    status: DerivedStatus
    sections: list[SectionProgress] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return self.total - self.done


@dataclass(slots=True)
class PlanAuditReport:
    plan_id: int
    cycles: list[list[str]]
    done_with_unfinished_blocks: list[str]
    critical_path_length: int = 0  # populated by audit() via critical_path()

    @property
    def issues_total(self) -> int:
        return len(self.cycles) + len(self.done_with_unfinished_blocks)


@dataclass(slots=True)
class ChainEntry:
    """One task in a forward/reverse chain or critical path."""

    task_id: str
    title: str
    status: TaskStatus
    depth: int  # 0 = directly adjacent, increasing away from start


@dataclass(slots=True)
class CriticalPathResult:
    """Longest sequential chain of blocks-edges in a plan."""

    plan_id: int
    task_ids: list[str]  # ordered from source to sink
    chain: list[ChainEntry]  # same order, with metadata
    length: int  # number of tasks (0 = empty plan)


# --------------------------------------------------------------------------- #
# Internals                                                                     #
# --------------------------------------------------------------------------- #


def _require_plan(session: Session, plan_id: int) -> PlanModel:
    model = session.get(PlanModel, plan_id)
    if model is None:
        raise PlanNotFoundError(f"plan #{plan_id}")
    return model


def _derive_status(total: int, done: int, in_progress: int) -> DerivedStatus:
    if total == 0:
        return DerivedStatus.EMPTY
    if done == total:
        return DerivedStatus.DONE
    if in_progress > 0 or done > 0:
        return DerivedStatus.IN_PROGRESS
    return DerivedStatus.PENDING


# --------------------------------------------------------------------------- #
# recalc                                                                        #
# --------------------------------------------------------------------------- #


def recalc(session: Session, plan_id: int) -> PlanProgress:
    """Read derived progress from `section_totals` + `plan_totals` views."""
    plan = _require_plan(session, plan_id)

    sec_rows = session.execute(
        text(
            "SELECT s.row_id, s.letter, s.title, s.slug, s.position, "
            "       st.tasks_total, st.tasks_done, st.tasks_in_progress "
            "FROM plan_section s "
            "JOIN section_totals st ON st.section_id = s.row_id "
            "WHERE s.plan_id = :pid "
            "ORDER BY s.position"
        ),
        {"pid": plan_id},
    ).all()

    sections = [
        SectionProgress(
            section_id=row[0],
            letter=row[1],
            title=row[2],
            slug=row[3],
            position=row[4],
            total=int(row[5] or 0),
            done=int(row[6] or 0),
            in_progress=int(row[7] or 0),
            status=_derive_status(int(row[5] or 0), int(row[6] or 0), int(row[7] or 0)),
        )
        for row in sec_rows
    ]

    plan_row = session.execute(
        text(
            "SELECT tasks_total, tasks_done, tasks_in_progress "
            "FROM plan_totals WHERE plan_id = :pid"
        ),
        {"pid": plan_id},
    ).one_or_none()
    total = int(plan_row[0] or 0) if plan_row else 0
    done = int(plan_row[1] or 0) if plan_row else 0
    in_progress = int(plan_row[2] or 0) if plan_row else 0

    return PlanProgress(
        plan_id=plan_id,
        scope=plan.scope,
        total=total,
        done=done,
        in_progress=in_progress,
        status=_derive_status(total, done, in_progress),
        sections=sections,
    )


# --------------------------------------------------------------------------- #
# ready                                                                         #
# --------------------------------------------------------------------------- #


def ready(session: Session, plan_id: int, *, limit: int | None = None) -> list[Task]:
    """Tasks ready to work on: pending + all blocking deps done.

    Reads `ready_tasks` view, filters to the given plan, sorts by priority
    (critical > high > medium > low) then by `task_id` for stability.
    """
    _require_plan(session, plan_id)

    rows = session.execute(
        text("SELECT row_id FROM ready_tasks WHERE plan_id = :pid"),
        {"pid": plan_id},
    ).all()
    if not rows:
        return []

    row_ids = [r[0] for r in rows]
    repo = TaskRepository(session)
    items = [t for t in (repo.get(rid) for rid in row_ids) if t is not None]

    items.sort(key=lambda t: (_PRIORITY_ORDER.get(t.priority.value, 99), t.task_id))
    if limit is not None:
        items = items[:limit]
    return items


# --------------------------------------------------------------------------- #
# audit                                                                         #
# --------------------------------------------------------------------------- #


def _find_cycles(adjacency: dict[int, list[int]]) -> list[list[int]]:
    """Return all simple cycles in the directed graph using iterative DFS.

    Adjacency keys are task row_ids; values are lists of `to_task_id`s for
    edges with `kind='blocks'`. Each cycle is reported once (canonicalized
    by rotating to its minimum element).
    """
    seen_cycles: set[tuple[int, ...]] = set()
    result: list[list[int]] = []

    color: dict[int, int] = {}  # 0=unvisited, 1=on-stack, 2=done
    stack: list[tuple[int, list[int]]] = []  # (node, iter-state)

    def canonicalize(cycle: list[int]) -> tuple[int, ...]:
        n = len(cycle)
        i_min = min(range(n), key=lambda i: cycle[i])
        return tuple(cycle[i_min:] + cycle[:i_min])

    for start in adjacency:
        if color.get(start, 0) != 0:
            continue
        path: list[int] = []
        stack = [(start, list(adjacency.get(start, [])))]
        color[start] = 1
        path.append(start)

        while stack:
            node, neighbors = stack[-1]
            if not neighbors:
                color[node] = 2
                stack.pop()
                if path:
                    path.pop()
                continue
            nxt = neighbors.pop()
            c = color.get(nxt, 0)
            if c == 1:
                # Back-edge → cycle. Slice path from first occurrence of `nxt`.
                idx = path.index(nxt)
                cycle = path[idx:]
                key = canonicalize(cycle)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    result.append(list(key))
            elif c == 0:
                color[nxt] = 1
                path.append(nxt)
                stack.append((nxt, list(adjacency.get(nxt, []))))
    return result


def audit(session: Session, plan_id: int) -> PlanAuditReport:
    """Integrity checks for the plan: cycle detection + done-drift."""
    _require_plan(session, plan_id)

    # Load tasks (row_id -> task_id, status) for this plan.
    task_rows = session.execute(
        select(TaskModel.row_id, TaskModel.task_id, TaskModel.status).where(
            TaskModel.plan_id == plan_id
        )
    ).all()
    row_id_to_task: dict[int, tuple[str, str]] = {r[0]: (r[1], r[2]) for r in task_rows}

    # Load `blocks` edges between tasks of this plan.
    plan_row_ids = list(row_id_to_task.keys())
    adjacency: dict[int, list[int]] = {rid: [] for rid in plan_row_ids}
    if plan_row_ids:
        dep_rows = session.execute(
            select(DependencyModel.from_task_id, DependencyModel.to_task_id).where(
                DependencyModel.kind == "blocks",
                DependencyModel.from_task_id.in_(plan_row_ids),
                DependencyModel.to_task_id.in_(plan_row_ids),
            )
        ).all()
        for src, dst in dep_rows:
            adjacency[src].append(dst)

    # Cycles.
    cycles_rids = _find_cycles(adjacency)
    cycles = [
        [row_id_to_task[rid][0] for rid in cyc if rid in row_id_to_task] for cyc in cycles_rids
    ]

    # Drift: done tasks whose blocks-deps are not all done.
    done_with_unfinished: list[str] = []
    for src_rid, neighbors in adjacency.items():
        src_task_id, src_status = row_id_to_task[src_rid]
        if src_status != TaskStatus.DONE.value:
            continue
        for dst_rid in neighbors:
            entry = row_id_to_task.get(dst_rid)
            if entry is None:
                continue
            if entry[1] != TaskStatus.DONE.value:
                done_with_unfinished.append(src_task_id)
                break

    cp_length = critical_path(session, plan_id).length

    return PlanAuditReport(
        plan_id=plan_id,
        cycles=cycles,
        done_with_unfinished_blocks=done_with_unfinished,
        critical_path_length=cp_length,
    )


# --------------------------------------------------------------------------- #
# export                                                                        #
# --------------------------------------------------------------------------- #


def _render_progress_overview(progress: PlanProgress) -> str:
    """Markdown table per [standards/task-plan.md §4.2]."""
    lines = [
        "## Progress Overview",
        "",
        "| Section | Total | Done | Remaining | Status |",
        "|:--------|------:|-----:|----------:|:-------|",
    ]
    for sec in progress.sections:
        lines.append(
            f"| {sec.letter}: {sec.title} | {sec.total} | {sec.done} | "
            f"{sec.remaining} | {sec.status.value} |"
        )
    lines.append(
        f"| **TOTAL** | **{progress.total}** | **{progress.done}** | "
        f"**{progress.remaining}** | {progress.status.value} |"
    )
    return "\n".join(lines) + "\n"


def _render_next_batch(ready_tasks: list[Task], *, limit: int = 7) -> str:
    """Markdown bullet list of the next N ready tasks (priority-ordered)."""
    lines = ["## Next Batch", ""]
    if not ready_tasks:
        lines.append("_No ready tasks._")
        return "\n".join(lines) + "\n"
    for t in ready_tasks[:limit]:
        lines.append(f"- **{t.task_id}** ({t.priority.value}/{t.type.value}) — {t.title}")
    return "\n".join(lines) + "\n"


def _mermaid_node_id(task_id: str) -> str:
    """Mermaid-safe node id: replace hyphens with underscores."""
    return task_id.replace("-", "_")


def _render_dependency_graph(
    plan_id: int, session: Session, row_id_to_task_id: dict[int, str]
) -> str:
    """Mermaid graph TD with one node per task and edges from blocker → blocked."""
    lines = ["## Dependency Graph", "", "```mermaid", "graph TD"]
    # Nodes (sorted for stable output).
    for tid in sorted(row_id_to_task_id.values()):
        node = _mermaid_node_id(tid)
        lines.append(f"  {node}[{tid}]")
    # Edges: dependency rows where from blocks to → to is the blocker.
    # Convention in DATA_MODEL §3.8: from_task is *blocked by* to_task.
    # In Mermaid we draw blocker → blocked, so the arrow goes to_task → from_task.
    if row_id_to_task_id:
        rids = list(row_id_to_task_id.keys())
        dep_rows = session.execute(
            select(DependencyModel.from_task_id, DependencyModel.to_task_id).where(
                DependencyModel.kind == "blocks",
                DependencyModel.from_task_id.in_(rids),
                DependencyModel.to_task_id.in_(rids),
            )
        ).all()
        edges = sorted((row_id_to_task_id[src], row_id_to_task_id[dst]) for src, dst in dep_rows)
        for blocked_id, blocker_id in edges:
            lines.append(f"  {_mermaid_node_id(blocker_id)} --> {_mermaid_node_id(blocked_id)}")
    lines.append("```")
    return "\n".join(lines) + "\n"


def export(session: Session, plan_id: int) -> dict[str, str]:
    """Render markdown projections for the plan.

    Returns a dict with keys: `progress_overview`, `next_batch`,
    `dependency_graph`. Callers compose them into the execution-plan markdown
    file (or the parent doc, in split format).
    """
    _require_plan(session, plan_id)

    progress = recalc(session, plan_id)
    ready_tasks = ready(session, plan_id)

    # Build row_id -> task_id map for the dependency graph.
    task_rows = session.execute(
        select(TaskModel.row_id, TaskModel.task_id).where(TaskModel.plan_id == plan_id)
    ).all()
    row_id_to_task_id = {r[0]: r[1] for r in task_rows}

    return {
        "progress_overview": _render_progress_overview(progress),
        "next_batch": _render_next_batch(ready_tasks),
        "dependency_graph": _render_dependency_graph(plan_id, session, row_id_to_task_id),
    }


# --------------------------------------------------------------------------- #
# Graph queries (COD-021)                                                       #
# --------------------------------------------------------------------------- #

# SQL recursive CTE templates.
# Dependency semantics: (from_task_id=B, to_task_id=A, kind='blocks')
# means A must complete before B.  Execution-order edges: A → B.
#
# forward_chain(X): what must complete BEFORE X.
#   Start at X, follow from→to edges (i.e. d.to_task_id).
#   Each hop moves to a prerequisite.
#
# reverse_chain(X): what X unblocks (dependents).
#   Start at X, follow to→from edges (i.e. d.from_task_id).
#   Each hop moves to a task that depends on X.

_FORWARD_CHAIN_SQL = text("""
WITH RECURSIVE chain(row_id, depth) AS (
    SELECT t.row_id, 0
    FROM task t
    WHERE t.task_id = :task_id
  UNION ALL
    SELECT d.to_task_id, chain.depth + 1
    FROM chain
    JOIN dependency d
      ON d.from_task_id = chain.row_id
     AND d.kind = 'blocks'
)
SELECT DISTINCT t.row_id, t.task_id, t.title, t.status, MIN(chain.depth) AS depth
FROM chain
JOIN task t ON t.row_id = chain.row_id
WHERE chain.row_id != (SELECT row_id FROM task WHERE task_id = :task_id)
GROUP BY t.row_id, t.task_id, t.title, t.status
ORDER BY depth
""")

_REVERSE_CHAIN_SQL = text("""
WITH RECURSIVE chain(row_id, depth) AS (
    SELECT t.row_id, 0
    FROM task t
    WHERE t.task_id = :task_id
  UNION ALL
    SELECT d.from_task_id, chain.depth + 1
    FROM chain
    JOIN dependency d
      ON d.to_task_id = chain.row_id
     AND d.kind = 'blocks'
)
SELECT DISTINCT t.row_id, t.task_id, t.title, t.status, MIN(chain.depth) AS depth
FROM chain
JOIN task t ON t.row_id = chain.row_id
WHERE chain.row_id != (SELECT row_id FROM task WHERE task_id = :task_id)
GROUP BY t.row_id, t.task_id, t.title, t.status
ORDER BY depth
""")

# CTE: longest prerequisite-chain depth for each task in a plan.
# depth[T] = max number of edges from any source (no-prereq task) to T.
_DEPTH_CTE_SQL = text("""
WITH RECURSIVE depths(task_row_id, depth) AS (
    -- Sources: tasks with no blocks-prerequisite within this plan.
    SELECT t.row_id, 0
    FROM task t
    WHERE t.plan_id = :plan_id
      AND NOT EXISTS (
          SELECT 1
          FROM dependency d
          JOIN task src ON src.row_id = d.to_task_id AND src.plan_id = :plan_id
          WHERE d.from_task_id = t.row_id AND d.kind = 'blocks'
      )
  UNION ALL
    -- Extend: follow to→from edges (prerequisite → dependent).
    SELECT d.from_task_id, depths.depth + 1
    FROM depths
    JOIN dependency d
      ON d.to_task_id = depths.task_row_id
     AND d.kind = 'blocks'
    JOIN task dep_task ON dep_task.row_id = d.from_task_id AND dep_task.plan_id = :plan_id
)
SELECT task_row_id, MAX(depth) AS max_depth
FROM depths
GROUP BY task_row_id
ORDER BY max_depth DESC
""")


def forward_chain(session: Session, task_id: str) -> list[ChainEntry]:
    """Prerequisites of *task_id*: tasks that must complete BEFORE it.

    Follows `blocks`-edges transitively. Ordered by depth (depth=1 = direct
    prerequisite). Raises `TaskNotFoundInPlanError` if task_id unknown.
    Per [user-stories-graph.md §6]: ``cod-doc graph forward <task_id>``.
    """
    row = session.execute(
        select(TaskModel.row_id).where(TaskModel.task_id == task_id)
    ).scalar_one_or_none()
    if row is None:
        raise TaskNotFoundInPlanError(task_id)

    rows = session.execute(_FORWARD_CHAIN_SQL, {"task_id": task_id}).all()
    return [
        ChainEntry(
            task_id=r[1],
            title=r[2],
            status=TaskStatus(r[3]),
            depth=r[4],
        )
        for r in rows
    ]


def reverse_chain(session: Session, task_id: str) -> list[ChainEntry]:
    """Dependents of *task_id*: tasks unblocked when it completes.

    Follows `blocks`-edges transitively in the reverse direction. Depth=1 =
    directly blocked. Raises `TaskNotFoundInPlanError` if task_id unknown.
    Per [user-stories-graph.md §6]: ``cod-doc graph reverse <task_id>``.
    """
    row = session.execute(
        select(TaskModel.row_id).where(TaskModel.task_id == task_id)
    ).scalar_one_or_none()
    if row is None:
        raise TaskNotFoundInPlanError(task_id)

    rows = session.execute(_REVERSE_CHAIN_SQL, {"task_id": task_id}).all()
    return [
        ChainEntry(
            task_id=r[1],
            title=r[2],
            status=TaskStatus(r[3]),
            depth=r[4],
        )
        for r in rows
    ]


def critical_path(session: Session, plan_id: int) -> CriticalPathResult:
    """Longest sequential `blocks`-chain in the plan.

    Uses a recursive CTE to compute the maximum prerequisite-chain depth for
    every task, then backtracks from the deepest task through its highest-depth
    predecessor to reconstruct the critical path in execution order.

    Returns `CriticalPathResult` with `task_ids` (ordered source→sink),
    `chain` (list of ChainEntry with depth metadata), and `length` (task count).
    An empty plan returns length=0, task_ids=[].
    """
    _require_plan(session, plan_id)

    depth_rows = session.execute(_DEPTH_CTE_SQL, {"plan_id": plan_id}).all()
    if not depth_rows:
        return CriticalPathResult(plan_id=plan_id, task_ids=[], chain=[], length=0)

    # Map row_id → max_depth.
    depth_map: dict[int, int] = {r[0]: r[1] for r in depth_rows}

    # Load full task info for the plan.
    task_rows = session.execute(
        select(TaskModel.row_id, TaskModel.task_id, TaskModel.title, TaskModel.status).where(
            TaskModel.plan_id == plan_id
        )
    ).all()
    info: dict[int, tuple[str, str, str]] = {r[0]: (r[1], r[2], r[3]) for r in task_rows}

    # Load blocks-edges within the plan.
    plan_rids = list(info.keys())
    prereqs: dict[int, list[int]] = {rid: [] for rid in plan_rids}  # rid→[its prerequisite rids]
    if plan_rids:
        dep_rows = session.execute(
            select(DependencyModel.from_task_id, DependencyModel.to_task_id).where(
                DependencyModel.kind == "blocks",
                DependencyModel.from_task_id.in_(plan_rids),
                DependencyModel.to_task_id.in_(plan_rids),
            )
        ).all()
        for blocked, blocker in dep_rows:
            prereqs[blocked].append(blocker)

    # The critical path endpoint: task with the greatest max_depth.
    sink_rid = max(depth_map, key=lambda r: depth_map[r])
    sink_depth = depth_map[sink_rid]

    if sink_depth == 0:
        # All tasks are independent sources; critical path is length-1 (pick first alphabetically).
        source_rid = min(plan_rids, key=lambda r: info[r][0])
        task_id, title, status = info[source_rid]
        return CriticalPathResult(
            plan_id=plan_id,
            task_ids=[task_id],
            chain=[ChainEntry(task_id=task_id, title=title, status=TaskStatus(status), depth=0)],
            length=1,
        )

    # Backtrack: from sink, greedily pick the predecessor with depth == current_depth - 1.
    path_rids: list[int] = [sink_rid]
    current = sink_rid
    while depth_map.get(current, 0) > 0:
        best = None
        for prereq_rid in prereqs[current]:
            if prereq_rid not in depth_map:
                continue
            if depth_map[prereq_rid] == depth_map[current] - 1 and (
                best is None or info[prereq_rid][0] < info[best][0]
            ):
                best = prereq_rid
        if best is None:
            break
        path_rids.append(best)
        current = best

    path_rids.reverse()  # source → sink order

    chain = []
    for depth_idx, rid in enumerate(path_rids):
        task_id, title, status = info[rid]
        chain.append(
            ChainEntry(
                task_id=task_id,
                title=title,
                status=TaskStatus(status),
                depth=depth_idx,
            )
        )

    return CriticalPathResult(
        plan_id=plan_id,
        task_ids=[e.task_id for e in chain],
        chain=chain,
        length=len(chain),
    )


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
    "ready",
    "recalc",
    "reverse_chain",
]
