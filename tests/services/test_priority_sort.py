"""COD-070: priority_sql_order maps string column → semantic rank.

Without this helper, ORDER BY TaskModel.priority returns alphabetic order
('critical' < 'high' < 'low' < 'medium'), which puts low-priority tasks
ahead of medium ones — surprising and wrong. The helper produces the
intended order: critical → high → medium → low.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.infra.sql_helpers import priority_sql_order
from cod_doc.services import task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="prio", title="Prio", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(
        project_id=proj.row_id, scope="prio-plan", created=now, last_updated=now
    )
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
    )
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def test_priority_sql_order_yields_semantic_ranking(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        for tid, prio in [
            ("PR-001", Priority.LOW),
            ("PR-002", Priority.CRITICAL),
            ("PR-003", Priority.MEDIUM),
            ("PR-004", Priority.HIGH),
        ]:
            task_service.create(
                session,
                project_id=p,
                plan_id=pl,
                section_id=s,
                task_id=tid,
                title=f"task {tid}",
                type=TaskType.FEATURE,
                priority=prio,
                author="human:test",
            )

        ordered = session.execute(
            select(TaskModel.task_id)
            .where(TaskModel.project_id == p)
            .order_by(priority_sql_order(TaskModel.priority), TaskModel.task_id)
        ).scalars().all()

    # critical < high < medium < low
    assert ordered == ["PR-002", "PR-004", "PR-003", "PR-001"]


def test_unknown_priority_values_sink_to_bottom(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Defensive: an unexpected raw value (corrupted DB row) doesn't crash
    the query — it just sorts last."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="PR-001",
            title="known low",
            type=TaskType.FEATURE,
            priority=Priority.LOW,
            author="human:test",
        )
        # Manual UPDATE → simulate a bad value that bypassed the enum.
        session.execute(
            TaskModel.__table__.update()
            .where(TaskModel.task_id == "PR-001")
            .values(priority="undefined")
        )
        session.flush()

        ordered = session.execute(
            select(TaskModel.task_id)
            .where(TaskModel.project_id == p)
            .order_by(priority_sql_order(TaskModel.priority))
        ).scalars().all()
    assert ordered == ["PR-001"]
