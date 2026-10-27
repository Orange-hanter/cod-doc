"""Serializers: domain objects → JSON-able dicts for MCP/API/CLI surfaces.

Lives in the services layer so any surface (MCP tools, REST API, CLI, agent
service) can render a domain object without the services layer importing
upwards (ADO-041 / audit finding M6 — services must not depend on
``cod_doc.mcp.*``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Task
    from cod_doc.domain.roadmap import Roadmap


def task_to_dict(t: Task, session: Session | None = None) -> dict[str, Any]:
    """Render a Task domain object as a dict for MCP/JSON return.

    When ``session`` is provided, blocked_by/affects_files/story_id are
    populated from related tables (`dependency`, `affected_file`,
    `story_link`), and ``plan_scope`` / ``section_letter`` are resolved
    from `plan` / `plan_section` (RFC 27 F7 — один запрос на строку).
    Without session, they default to empty/None (legacy behaviour for
    callers that don't care about graph relations).
    """
    result: dict[str, Any] = {
        "task_id": t.task_id,
        "title": t.title,
        "status": t.status.value,
        "type": t.type.value,
        "priority": t.priority.value,
        "plan_id": t.plan_id,
        "section_id": t.section_id,
        "plan_scope": None,
        "section_letter": None,
        "description": t.description,
        "acceptance": t.acceptance,
        "blocked_reason": t.blocked_reason,
        "estimate_person_hours": t.estimate_person_hours,
        "blocked_by": [],
        "affects_files": [],
        "story_id": None,
        "created": t.created.isoformat() if t.created else None,
        "last_updated": t.last_updated.isoformat() if t.last_updated else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "completed_commit": t.completed_commit,
    }
    if session is not None and getattr(t, "row_id", None) is not None:
        from sqlalchemy import select

        from cod_doc.infra.models import (
            AffectedFileModel,
            DependencyModel,
            PlanModel,
            PlanSectionModel,
            TaskModel,
        )
        from cod_doc.services.story_lookup import story_id_for_task

        plan_row = session.execute(
            select(PlanModel.scope, PlanSectionModel.letter)
            .select_from(PlanModel)
            .outerjoin(PlanSectionModel, PlanSectionModel.row_id == t.section_id)
            .where(PlanModel.row_id == t.plan_id)
        ).one_or_none()
        if plan_row is not None:
            result["plan_scope"] = plan_row[0]
            result["section_letter"] = plan_row[1]
        result["blocked_by"] = list(
            session.execute(
                select(TaskModel.task_id)
                .join(DependencyModel, DependencyModel.to_task_id == TaskModel.row_id)
                .where(
                    DependencyModel.from_task_id == t.row_id,
                    DependencyModel.kind == "blocks",
                )
                .order_by(TaskModel.task_id)
            ).scalars()
        )
        result["affects_files"] = list(
            session.execute(
                select(AffectedFileModel.path)
                .where(AffectedFileModel.task_id == t.row_id)
                .order_by(AffectedFileModel.path)
            ).scalars()
        )
        result["story_id"] = story_id_for_task(session, project_id=t.project_id, task_id=t.task_id)
    return result


def roadmap_to_dict(roadmap: Roadmap, *, plan_scope: str | None = None) -> dict[str, Any]:
    """Дорожная карта (RFC 36 §3.2.5) в JSON-форму — одна на MCP, CLI, REST и веб (C-04).

    Даты — ISO-строки или ``None``; ``meaning`` повторяет главное правило
    прямо в ответе: дата — прогноз по пропускной способности, не срок (C-19).
    ``plan_scope`` оставляет строки и полосы одного плана; позиции и даты —
    по-прежнему очереди всего проекта.
    """

    def keep(scope: str) -> bool:
        return plan_scope is None or scope == plan_scope

    def day(value: date | None) -> str | None:
        return None if value is None else value.isoformat()

    queue, forecast = roadmap.queue, roadmap.forecast
    dates = {row.task_id: row for row in forecast.rows}
    return {
        "meaning": (
            "Order is the plan; dates are a throughput forecast (p50/p85) of when "
            "the queue reaches a task, not deadlines."
        ),
        "queue": {
            "unit": queue.unit.value,
            "rows": [
                {
                    "position": row.position,
                    "task_id": row.task_id,
                    "title": row.title,
                    "plan_scope": row.plan_scope,
                    "section_letter": row.section_letter,
                    "status": row.status.value,
                    "priority": row.priority.value,
                    "pulled_by": list(row.pulled_by),
                    "flags": sorted(flag.value for flag in row.flags),
                    "weight": row.weight,
                    "duration_source": row.duration_source.value,
                    "p50": day(dates[row.task_id].p50),
                    "p85": day(dates[row.task_id].p85),
                }
                for row in queue.rows
                if keep(row.plan_scope)
            ],
            "plans": [
                {
                    "scope": bar.scope,
                    "principle": bar.principle,
                    "rank": bar.rank,
                    "first_position": bar.first_position,
                    "last_position": bar.last_position,
                    "open_count": bar.open_count,
                }
                for bar in queue.plans
                if keep(bar.scope)
            ],
        },
        "forecast": {
            "asof": forecast.asof.isoformat(),
            "sample": [
                {
                    "week_start": w.week_start.isoformat(),
                    "closed": w.closed,
                    "created": w.created,
                    "closed_weight": w.closed_weight,
                }
                for w in forecast.sample
            ],
            "plans": [
                {
                    "scope": p.scope,
                    "p50_start": day(p.p50_start),
                    "p50_finish": day(p.p50_finish),
                    "p85_finish": day(p.p85_finish),
                }
                for p in forecast.plans
                if keep(p.scope)
            ],
            "warnings": [w.value for w in forecast.warnings],
            "runs": forecast.runs,
            "seed": forecast.seed,
            "unit": forecast.unit.value,
        },
    }
