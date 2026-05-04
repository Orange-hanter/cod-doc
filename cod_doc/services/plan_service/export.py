"""Markdown projections — Progress Overview / Next Batch / Dependency Graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import DependencyModel, TaskModel

from ._internals import _require_plan
from .reads import ready, recalc

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Task

    from ._types import PlanProgress


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


def freeze_projection(
    session,  # type: ignore[no-untyped-def]
    plan_id: int,
    *,
    author: str,
    reason: str | None = None,
):
    """COD-052: snapshot the current projection markdown into a Document.

    Creates an immutable EXECUTION_LOG document with status=ACTIVE under the
    ``frozen/<scope>/<UTC timestamp>`` key. The document body is the three
    projection sections joined with H2 separators. Subsequent freezes create
    new entries — frozen snapshots are append-only history.

    Returns the new ``Document`` (so the caller can redirect / link).
    """
    from datetime import UTC, datetime

    from cod_doc.domain.entities import (
        DocumentStatus,
        DocumentType,
        Sensitivity,
    )
    from cod_doc.services import doc_service

    plan = _require_plan(session, plan_id)
    parts = export(session, plan_id)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    body = (
        f"# Frozen projection — {plan.scope} ({ts})\n\n"
        "## Progress overview\n\n"
        f"{parts['progress_overview'].strip()}\n\n"
        "## Next batch\n\n"
        f"{parts['next_batch'].strip()}\n\n"
        "## Dependency graph\n\n"
        f"{parts['dependency_graph'].strip()}\n"
    )
    doc_key = f"frozen/{plan.scope}/{ts}"
    return doc_service.create(
        session,
        project_id=plan.project_id,
        doc_key=doc_key,
        type=DocumentType.EXECUTION_LOG,
        status=DocumentStatus.ACTIVE,
        title=f"Frozen projection — {plan.scope} @ {ts}",
        author=author,
        owner=author,
        sensitivity=Sensitivity.INTERNAL,
        preamble=body,
        reason=reason or f"freeze:{plan.scope}",
    )
