"""Read-only queries — get_for_project / list_for_project / recalc / ready."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, text

from cod_doc.domain.entities import Plan, PlanSection, Task
from cod_doc.infra.models import PlanModel
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    TaskRepository,
)

from ._internals import _PRIORITY_ORDER, _derive_status, _require_plan
from ._types import PlanProgress, SectionProgress

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def get_by_scope(session: Session, scope: str) -> Plan | None:
    """Return a Plan by its unique ``scope`` slug, or None.

    Web handlers (story task-decomposition, master import) use this to look
    up a target plan by name without reaching into infra.repositories.
    """
    return PlanRepository(session).get_by_scope(scope)


def list_sections(session: Session, plan_id: int) -> list[PlanSection]:
    """Return the plan's sections in their stored order."""
    return PlanSectionRepository(session).list_for_plan(plan_id)


def get_for_project(session: Session, project_id: int, plan_id: int) -> Plan | None:
    """Return the plan iff it belongs to the project; None otherwise.

    Used by web handlers to reject cross-project plan access (404).
    """
    model = session.get(PlanModel, plan_id)
    if model is None or model.project_id != project_id:
        return None
    return Plan(
        row_id=model.row_id,
        project_id=model.project_id,
        scope=model.scope,
        principle=model.principle,
        module_id=model.module_id,
        parent_doc_id=model.parent_doc_id,
        completed_log_id=model.completed_log_id,
        created=model.created,
        last_updated=model.last_updated,
    )


def list_for_project(session: Session, project_id: int) -> list[Plan]:
    """All plans owned by the project, ordered by `created` (oldest first).

    Lightweight read helper for the overview dashboard (WEB-014); recalc
    is done lazily per-plan only when the caller actually needs progress.
    """
    stmt = (
        select(PlanModel)
        .where(PlanModel.project_id == project_id)
        .order_by(PlanModel.created.asc(), PlanModel.row_id.asc())
    )
    return [
        Plan(
            row_id=m.row_id,
            project_id=m.project_id,
            scope=m.scope,
            principle=m.principle,
            module_id=m.module_id,
            parent_doc_id=m.parent_doc_id,
            completed_log_id=m.completed_log_id,
            created=m.created,
            last_updated=m.last_updated,
        )
        for m in session.execute(stmt).scalars()
    ]


def recalc_for_project(session: Session, project_id: int) -> dict[int, PlanProgress]:
    """COD-075: O(1)-query progress for every plan in a project.

    Avoids the N+1 of calling ``recalc()`` per-plan during the overview
    render. Sections are *not* populated — overview only needs totals.
    Returns ``{plan_id: PlanProgress}`` keyed by ``Plan.row_id``.
    """
    rows = session.execute(
        text(
            "SELECT p.row_id, p.scope, "
            "       COALESCE(pt.tasks_total, 0), "
            "       COALESCE(pt.tasks_done, 0), "
            "       COALESCE(pt.tasks_in_progress, 0) "
            "FROM plan p "
            "LEFT JOIN plan_totals pt ON pt.plan_id = p.row_id "
            "WHERE p.project_id = :pid"
        ),
        {"pid": project_id},
    ).all()
    return {
        int(r[0]): PlanProgress(
            plan_id=int(r[0]),
            scope=str(r[1]),
            total=int(r[2]),
            done=int(r[3]),
            in_progress=int(r[4]),
            status=_derive_status(int(r[2]), int(r[3]), int(r[4])),
            sections=[],
        )
        for r in rows
    }


def ready_for_project(session: Session, project_id: int, *, limit: int | None = None) -> list[Task]:
    """COD-075: ready batch across all plans of a project — single SQL.

    Avoids the per-plan ``ready()`` loop on the overview page.
    """
    repo = TaskRepository(session)
    sql = (
        "SELECT t.row_id "
        "FROM ready_tasks rt "
        "JOIN task t ON t.row_id = rt.row_id "
        "JOIN plan p ON p.row_id = t.plan_id "
        "WHERE p.project_id = :pid "
        "ORDER BY CASE t.priority "
        "WHEN 'critical' THEN 0 "
        "WHEN 'high' THEN 1 "
        "WHEN 'medium' THEN 2 "
        "WHEN 'low' THEN 3 ELSE 99 END, "
        "t.task_id"
    )
    if limit is not None and limit > 0:
        sql += " LIMIT :lim"
        rows = session.execute(text(sql), {"pid": project_id, "lim": limit}).all()
    else:
        rows = session.execute(text(sql), {"pid": project_id}).all()
    out: list[Task] = []
    for r in rows:
        task = repo.get(int(r[0]))
        if task is not None:
            out.append(task)
    return out


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
