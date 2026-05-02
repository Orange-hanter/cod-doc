"""Graph queries — forward_chain / reverse_chain / critical_path (COD-021).

SQL recursive CTE templates.

Dependency semantics: (from_task_id=B, to_task_id=A, kind='blocks')
means A must complete before B. Execution-order edges: A → B.

forward_chain(X): what must complete BEFORE X.
  Start at X, follow from→to edges (i.e. d.to_task_id).
  Each hop moves to a prerequisite.

reverse_chain(X): what X unblocks (dependents).
  Start at X, follow to→from edges (i.e. d.from_task_id).
  Each hop moves to a task that depends on X.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, text

from cod_doc.domain.entities import TaskStatus
from cod_doc.infra.models import DependencyModel, TaskModel

from ._internals import _require_plan
from ._types import ChainEntry, CriticalPathResult, TaskNotFoundInPlanError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
