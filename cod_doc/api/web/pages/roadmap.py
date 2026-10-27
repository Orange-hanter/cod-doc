"""Дорожная карта проекта — ``/p/{slug}/roadmap`` (RFC 36 §3.5, ROAD-007/008).

Порядок работ всего проекта и прогноз «когда очередь дойдёт» по недельной
пропускной способности. Даты — не сроки: подпись шкалы и легенда говорят это
прямо (контракт C-19). Перестановка планов — кнопки «выше/ниже» у строки
плана (``plan_service.move_plan``, htmx), ответ — перерисованный холст.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.errors import NotFoundWebError, ValidationWebError
from cod_doc.api.web.fragments._shared import _is_htmx
from cod_doc.api.web.roadmap_chart import plans_chart
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.roadmap import FORECAST_WINDOW_WEEKS
from cod_doc.services import plan_service, roadmap_service
from cod_doc.services.plan_service import PlanNotFoundError
from cod_doc.services.serializers import roadmap_to_dict

if TYPE_CHECKING:
    from datetime import date

router = APIRouter()

ProjectDb = Annotated[tuple[Session, int], Depends(get_project_db)]

_WARNINGS = {
    "inflow_exceeds": "За окно создано больше задач, чем закрыто: даты оптимистичны.",
    "short_sample": "Истории меньше 4 недель: показан только порядок, без дат.",
    "zero_sample": "За окно не закрыто ни одной задачи: показан только порядок, без дат.",
    "beyond_horizon": "Хвост очереди дальше 26 недель: эти задачи без дат.",
}


def _context(
    session: Session,
    project_id: int,
    slug: str,
    *,
    asof: date,
    history: bool,
    backlog: bool,
) -> dict[str, Any]:
    try:
        built = roadmap_service.build(
            session, project_id=project_id, asof=asof, include_backlog=backlog
        )
    except roadmap_service.RoadmapCycleError as exc:
        return {"cycle": str(exc), "chart": None, "payload": None, "warnings": []}
    since = asof - timedelta(weeks=FORECAST_WINDOW_WEEKS)
    facts = (
        roadmap_service.history(session, project_id=project_id, asof=asof, since=since)
        if history
        else ()
    )
    plan_ids = {p.scope: p.row_id for p in plan_service.list_for_project(session, project_id)}
    chart = plans_chart(
        built,
        task_href=lambda task_id: f"/p/{slug}/tasks/{task_id}",
        plan_href=lambda scope: (
            f"/p/{slug}/plans/{plan_ids[scope]}" if plan_ids.get(scope) is not None else None
        ),
        history=facts,
        since=since if history else None,
    )
    payload = roadmap_to_dict(built)
    return {
        "cycle": None,
        "chart": chart,
        "payload": payload,
        "warnings": [_WARNINGS.get(w, w) for w in payload["forecast"]["warnings"]],
    }


@router.get("/p/{slug}/roadmap", response_class=HTMLResponse)
def roadmap_page(
    request: Request,
    slug: str,
    db: ProjectDb,
    history: bool = False,
    backlog: bool = False,
) -> HTMLResponse:
    """Очередь работ проекта и прогноз — Гант планов, таблица очереди, оговорки."""
    proj = get_project(slug)
    session, project_id = db
    asof = datetime.now(UTC).date()
    context = _context(
        session, project_id, proj.entry.name, asof=asof, history=history, backlog=backlog
    )
    return templates.TemplateResponse(
        request,
        "project/roadmap.html",
        {
            "project": {"name": proj.entry.name},
            "asof": asof,
            "weeks": FORECAST_WINDOW_WEEKS,
            "history": history,
            "backlog": backlog,
            **context,
        },
    )


@router.post("/p/{slug}/roadmap/plans/{scope}/move", response_class=HTMLResponse)
def roadmap_move_plan(
    request: Request,
    slug: str,
    scope: str,
    db: ProjectDb,
    direction: Annotated[Literal["up", "down"], Form()],
    history: Annotated[bool, Form()] = False,
    backlog: Annotated[bool, Form()] = False,
) -> Response:
    """Переставить план на одну строку дорожной карты выше/ниже (ROAD-008).

    Соседи — планы, видимые на дорожной карте (с открытыми задачами); план
    встаёт перед верхним соседом или после нижнего — ``plan_service.move_plan``,
    ревизия и событие ``plan.moved`` от ``human:web``.
    """
    proj = get_project(slug)
    session, project_id = db
    asof = datetime.now(UTC).date()
    try:
        visible = [
            bar.scope
            for bar in roadmap_service.sequence(
                session, project_id=project_id, include_backlog=backlog
            ).plans
        ]
    except roadmap_service.RoadmapCycleError as exc:
        raise ValidationWebError(str(exc)) from exc
    if scope not in visible:
        raise NotFoundWebError(f"План {scope} не на дорожной карте")
    i = visible.index(scope)
    neighbour = i - 1 if direction == "up" else i + 1
    if 0 <= neighbour < len(visible):
        where = {"before" if direction == "up" else "after": visible[neighbour]}
        try:
            plan_service.move_plan(
                session,
                project_id=project_id,
                plan_scope=scope,
                author="human:web",
                reason="web roadmap reorder",
                **where,
            )
            session.commit()
        except (PlanNotFoundError, ValueError) as exc:
            session.rollback()
            raise ValidationWebError(str(exc)) from exc

    if _is_htmx(request):
        context = _context(
            session, project_id, proj.entry.name, asof=asof, history=history, backlog=backlog
        )
        return templates.TemplateResponse(
            request,
            "_frag/roadmap_body.html",
            {
                "project": {"name": proj.entry.name},
                "asof": asof,
                "weeks": FORECAST_WINDOW_WEEKS,
                "history": history,
                "backlog": backlog,
                **context,
            },
        )
    query = "&".join(k for k, on in (("history=1", history), ("backlog=1", backlog)) if on)
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/roadmap" + (f"?{query}" if query else ""), status_code=303
    )
