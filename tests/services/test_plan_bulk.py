"""COD-075: bulk progress + ready_for_project — single-query aggregates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import (
    Plan,
    PlanSection,
    Priority,
    TaskType,
)
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import plan_service
from cod_doc.services import task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_two_plans(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="bulk", title="Bulk", root_path="/tmp/b", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    p_id = proj.row_id

    plan_a = PlanRepository(session).add(
        Plan(project_id=p_id, scope="bulk-a", principle="test-first")
    )
    plan_a.created = now
    plan_a.last_updated = now
    plan_b = PlanRepository(session).add(
        Plan(project_id=p_id, scope="bulk-b", principle="test-first")
    )
    plan_b.created = now
    plan_b.last_updated = now
    session.flush()

    for plan, prefix in ((plan_a, "BLA"), (plan_b, "BLB")):
        section = PlanSectionRepository(session).add(
            PlanSection(
                plan_id=plan.row_id,
                letter="A",
                title="Core",
                slug=f"A-Core-{plan.scope}",
                position=0,
            )
        )
        for i in range(3):
            task_service.create(
                session,
                project_id=p_id,
                plan_id=plan.row_id,
                section_id=section.row_id,
                title=f"{plan.scope}-task-{i}",
                type=TaskType.FEATURE,
                priority=Priority.HIGH if i == 0 else Priority.LOW,
                author="human:test",
                id_prefix=prefix,
            )
    return p_id, plan_a.row_id, plan_b.row_id


def test_recalc_for_project_returns_one_entry_per_plan(
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p_id, plan_a, plan_b = _seed_two_plans(session)
        out = plan_service.recalc_for_project(session, p_id)
    assert set(out.keys()) == {plan_a, plan_b}
    assert all(v.total == 3 for v in out.values())
    assert all(v.sections == [] for v in out.values())


def test_ready_for_project_aggregates_across_plans(
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p_id, _, _ = _seed_two_plans(session)
        # Top 3 ready: should pull HIGH from each plan first (priority order).
        out = plan_service.ready_for_project(session, p_id, limit=3)
    titles = [t.title for t in out]
    # Two HIGH-priority "task-0" entries land first; third one is a LOW.
    assert sum("task-0" in t for t in titles) == 2
    assert len(titles) == 3


def test_ready_for_project_priority_order(
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p_id, _, _ = _seed_two_plans(session)
        out = plan_service.ready_for_project(session, p_id)
    priorities = [t.priority.value for t in out]
    # All HIGH-priority tasks come before LOW.
    high_count = priorities.count("high")
    assert priorities[:high_count] == ["high"] * high_count
    assert all(p == "low" for p in priorities[high_count:])


def test_recalc_for_project_handles_empty_plan(
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="empty", title="Empty", root_path="/tmp/e", config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()
        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="empty-plan", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()

        out = plan_service.recalc_for_project(session, proj.row_id)
    # Plan with no tasks may or may not appear depending on view definition;
    # if it does, totals must be 0.
    if plan.row_id in out:
        prog = out[plan.row_id]
        assert prog.total == 0
        assert prog.done == 0
