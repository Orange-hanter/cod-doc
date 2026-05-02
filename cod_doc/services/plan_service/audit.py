"""Plan integrity audit — cycle detection + done-drift check."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import TaskStatus
from cod_doc.infra.models import DependencyModel, TaskModel

from ._internals import _require_plan
from ._types import PlanAuditReport
from .graph import critical_path

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
