"""COD-012 / RFL-074: PlanService — ready."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import plan_service as plans
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_plan_with_sections(
    session: Session, sections: list[tuple[str, str, str]] | None = None
) -> tuple[int, int, dict[str, int]]:
    sections = sections or [("A", "Data Core", "A-Data-Core")]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(
        project_id=proj.row_id,
        scope="p-plan",
        principle="test-first",
        created=now,
        last_updated=now,
    )
    session.add(plan)
    session.flush()
    sec_ids: dict[str, int] = {}
    for i, (letter, title, slug) in enumerate(sections):
        sec = PlanSectionModel(
            plan_id=plan.row_id, letter=letter, title=title, slug=slug, position=i
        )
        session.add(sec)
        session.flush()
        sec_ids[letter] = sec.row_id
    return proj.row_id, plan.row_id, sec_ids


def _seed_task(
    session: Session,
    *,
    proj_id: int,
    plan_id: int,
    section_id: int,
    task_id: str,
    status: TaskStatus = TaskStatus.PENDING,
    priority: Priority = Priority.MEDIUM,
):  # type: ignore[no-untyped-def]
    t = tasks.create(
        session,
        project_id=proj_id,
        plan_id=plan_id,
        section_id=section_id,
        task_id=task_id,
        title=f"Task {task_id}",
        type=TaskType.FEATURE,
        priority=priority,
        author="human:test",
    )
    if status is not TaskStatus.PENDING:
        tasks.update_status(
            session, task_id=task_id, new_status=status, author="human:test", force=True
        )
    return t


def test_ready_returns_pending_tasks_with_done_deps(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        a = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        b = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()

        ready = plans.ready(session, plan_id)
        assert {t.task_id for t in ready} == {"PLN-001"}

        tasks.complete(session, task_id="PLN-001", author="x")
        ready = plans.ready(session, plan_id)
        assert {t.task_id for t in ready} == {"PLN-002"}


def test_ready_excludes_in_progress_and_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-001",
            status=TaskStatus.IN_PROGRESS,
        )
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        tasks.complete(session, task_id="PLN-002", author="x")
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-003")

        ready = plans.ready(session, plan_id)
        assert {t.task_id for t in ready} == {"PLN-003"}


def test_ready_scoped_to_plan(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ready() must not leak tasks from other plans."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")

        now = datetime.now(UTC)
        plan2 = PlanModel(project_id=p, scope="other-plan", created=now, last_updated=now)
        session.add(plan2)
        session.flush()
        sec2 = PlanSectionModel(plan_id=plan2.row_id, letter="A", title="X", slug="A-X", position=0)
        session.add(sec2)
        session.flush()
        _seed_task(
            session, proj_id=p, plan_id=plan2.row_id, section_id=sec2.row_id, task_id="QQ-001"
        )

        ready = plans.ready(session, plan_id)
        assert {t.task_id for t in ready} == {"PLN-001"}


def test_ready_priority_order(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ready() returns tasks priority-sorted: critical > high > medium > low, then by task_id."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-001",
            priority=Priority.LOW,
        )
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-002",
            priority=Priority.CRITICAL,
        )
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-003",
            priority=Priority.MEDIUM,
        )

        ready = plans.ready(session, plan_id)
        assert [t.task_id for t in ready] == ["PLN-002", "PLN-003", "PLN-001"]


def test_ready_respects_limit(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        for n in range(1, 6):
            _seed_task(
                session,
                proj_id=p,
                plan_id=plan_id,
                section_id=secs["A"],
                task_id=f"PLN-00{n}",
            )
        ready = plans.ready(session, plan_id, limit=3)
        assert len(ready) == 3
