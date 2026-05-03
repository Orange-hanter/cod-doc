"""COD-012 / RFL-074: PlanService — recalc."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
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
        tasks.update_status(session, task_id=task_id, new_status=status, author="human:test")
    return t


def test_recalc_empty_plan_is_empty(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _, plan_id, _ = _seed_plan_with_sections(session)
        progress = plans.recalc(session, plan_id)

        assert progress.plan_id == plan_id
        assert progress.total == 0
        assert progress.done == 0
        assert progress.status is plans.DerivedStatus.EMPTY
        assert len(progress.sections) == 1
        assert progress.sections[0].status is plans.DerivedStatus.EMPTY


def test_recalc_section_pending_and_in_progress(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(
            session, [("A", "Core", "A-Core"), ("B", "Svc", "B-Svc")]
        )
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["B"],
            task_id="PLN-010",
            status=TaskStatus.IN_PROGRESS,
        )
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["B"], task_id="PLN-011")

        progress = plans.recalc(session, plan_id)
        by_letter = {s.letter: s for s in progress.sections}

        assert by_letter["A"].status is plans.DerivedStatus.PENDING
        assert by_letter["A"].total == 2 and by_letter["A"].done == 0
        assert by_letter["B"].status is plans.DerivedStatus.IN_PROGRESS
        assert by_letter["B"].in_progress == 1
        assert progress.status is plans.DerivedStatus.IN_PROGRESS
        assert progress.total == 4
        assert progress.remaining == 4


def test_recalc_section_done_when_all_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        tasks.complete(session, task_id="PLN-001", author="x")
        tasks.complete(session, task_id="PLN-002", author="x")

        progress = plans.recalc(session, plan_id)
        assert progress.status is plans.DerivedStatus.DONE
        assert progress.sections[0].status is plans.DerivedStatus.DONE
        assert progress.done == 2 and progress.remaining == 0


def test_recalc_partial_done_is_in_progress(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Section with some tasks done but others pending = in-progress."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        tasks.complete(session, task_id="PLN-001", author="x")

        progress = plans.recalc(session, plan_id)
        assert progress.sections[0].status is plans.DerivedStatus.IN_PROGRESS
        assert progress.status is plans.DerivedStatus.IN_PROGRESS


def test_recalc_unknown_plan_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(plans.PlanNotFoundError):
        plans.recalc(session, 9999)
