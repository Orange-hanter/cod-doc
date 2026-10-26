"""Plan list + plan detail handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db, try_open_project_db
from cod_doc.api.web.back_nav import back_nav, entity_labels
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.roadmap_chart import Chart, sections_chart
from cod_doc.api.web.templates_env import templates
from cod_doc.services import plan_service as plans
from cod_doc.services import roadmap_service
from cod_doc.services import task_service as tasks

router = APIRouter()

PLAN_READY_LIMIT = 7


@router.get("/p/{slug}/plans", response_class=HTMLResponse)
def plans_list(request: Request, slug: str) -> HTMLResponse:
    """List all plans of the project with current progress."""
    proj = get_project(slug)
    rows: list[dict[str, Any]] = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            for plan in plans.list_for_project(session, project_db_id):
                assert plan.row_id is not None
                progress = plans.recalc(session, plan.row_id, project_id=project_db_id)
                rows.append(
                    {
                        "plan_id": plan.row_id,
                        "scope": plan.scope,
                        "principle": plan.principle,
                        "total": progress.total,
                        "done": progress.done,
                        "in_progress": progress.in_progress,
                        "cancelled": progress.cancelled,
                        "remaining": progress.remaining,
                        "status": progress.status.value,
                        "percent": progress.pct_closed,
                        "last_updated": plan.last_updated,
                    }
                )
    return templates.TemplateResponse(
        request,
        "project/plans_list.html",
        {
            "project": {"name": proj.entry.name},
            "plans": rows,
            "db_available": db_available,
        },
    )


def _timeline(
    session: Session,
    project_id: int,
    plan_id: int,
    *,
    plan_scope: str,
    sections: list[tuple[str, str]],
    slug: str,
) -> dict[str, Any]:
    """ADO-137: Гант плана — секции × время, факт и прогноз (RFC 36 §3.5).

    Факт — ``created → completed_at``, в работе — до сегодня; открытым задачам
    — прогноз очереди всего проекта. Цикл в графе — только факт и пояснение.
    """
    asof = datetime.now(UTC).date()
    try:
        built = roadmap_service.build(session, project_id=project_id, asof=asof)
        cycle = None
    except roadmap_service.RoadmapCycleError as exc:
        built, cycle = None, str(exc)
    chart: Chart = sections_chart(
        built,
        plan_scope=plan_scope,
        sections=sections,
        history=roadmap_service.history(session, project_id=project_id, asof=asof, plan_id=plan_id),
        asof=asof,
        task_href=lambda task_id: f"/p/{slug}/tasks/{task_id}",
    )
    return {"chart": chart, "cycle": cycle}


@router.get("/p/{slug}/plans/{plan_id}", response_class=HTMLResponse)
def plan_show(
    request: Request,
    slug: str,
    plan_id: int,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """Plan detail: progress overview + ready batch + Mermaid graph."""
    proj = get_project(slug)
    session, project_db_id = db

    plan_dom = plans.get_for_project(session, plan_id, project_id=project_db_id)
    if plan_dom is None:
        raise HTTPException(404, f"Plan не найден в проекте: {plan_id}")

    progress = plans.recalc(session, plan_id, project_id=project_db_id)
    # ADO-149: соседи — планы в порядке списка планов. Ключ — row_id, а не
    # scope: адрес страницы плана — row_id, scope лишь подпись.
    plan_scopes = {str(p.row_id): p.scope for p in plans.list_for_project(session, project_db_id)}
    ready_tasks = plans.ready(session, plan_id, limit=PLAN_READY_LIMIT, project_id=project_db_id)
    exported = plans.export(session, plan_id, project_id=project_db_id)

    # Group tasks by section so the template can render one collapsible
    # block per section without re-querying. list_for_plan is already
    # ordered by (section_id, task_id) so iteration preserves layout.
    tasks_by_section: dict[int, list[dict[str, Any]]] = {}
    for t in tasks.list_for_plan(session, plan_id, project_id=project_db_id):
        tasks_by_section.setdefault(t.section_id, []).append(
            {
                "task_id": t.task_id,
                "title": t.title,
                "type": t.type.value,
                "status": t.status.value,
                "priority": t.priority.value,
            }
        )

    return templates.TemplateResponse(
        request,
        "project/plan_show.html",
        {
            "project": {"name": proj.entry.name},
            "plan": {
                "plan_id": plan_id,
                "scope": plan_dom.scope,
                "principle": plan_dom.principle,
                "status": progress.status.value,
                "total": progress.total,
                "done": progress.done,
                "in_progress": progress.in_progress,
                "cancelled": progress.cancelled,
                "remaining": progress.remaining,
                "percent": progress.pct_closed,
            },
            "sections": [
                {
                    "section_id": s.section_id,
                    "letter": s.letter,
                    "title": s.title,
                    "slug": s.slug,
                    "total": s.total,
                    "done": s.done,
                    "in_progress": s.in_progress,
                    "cancelled": s.cancelled,
                    "remaining": s.remaining,
                    "status": s.status.value,
                    "percent": s.pct_closed,
                    "tasks": tasks_by_section.get(s.section_id, []),
                }
                for s in progress.sections
            ],
            "ready": [
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "type": t.type.value,
                    "status": t.status.value,
                    "priority": t.priority.value,
                    "section_id": t.section_id,
                }
                for t in ready_tasks
            ],
            "exported": exported,
            "timeline": _timeline(
                session,
                project_db_id,
                plan_id,
                plan_scope=plan_dom.scope,
                sections=[(s.letter, s.title) for s in progress.sections],
                slug=proj.entry.name,
            ),
            "dependency_graph_html": render_markdown(exported.get("dependency_graph", "")),
            "nav": back_nav(
                request,
                proj.entry.name,
                tab="plans",
                order=list(plan_scopes),
                current=str(plan_id),
                href=lambda key: f"/p/{proj.entry.name}/plans/{key}",
                entity_label=entity_labels(session, project_db_id),
                label=plan_scopes.__getitem__,
            ),
        },
    )


@router.post("/p/{slug}/plans/{plan_id}/freeze", response_class=HTMLResponse)
def plan_freeze(
    request: Request,
    slug: str,
    plan_id: int,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """COD-052: snapshot the current projection into an EXECUTION_LOG document."""
    proj = get_project(slug)
    session, project_db_id = db
    plan_dom = plans.get_for_project(session, plan_id, project_id=project_db_id)
    if plan_dom is None:
        raise HTTPException(404, f"Plan не найден в проекте: {plan_id}")
    frozen = plans.freeze_projection(session, plan_id, author="human:web", project_id=project_db_id)
    session.commit()
    # Redirect to the new frozen document so the user can read/share it.
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{frozen.doc_key}",
        status_code=303,
    )
