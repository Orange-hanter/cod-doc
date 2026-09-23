"""ADO-078: отменённая реализующая задача не держит стори открытой.

Тот же дефект, что в `plan_service`, только в другом выражении:
`tasks_done == tasks_total` (`story_service/coverage.py`). Стори, чья
последняя задача отменена, не доходила до DELIVERED никогда — «поставлено»
требовало, чтобы отменённая задача стала `done`, то есть невозможного.

Отличие от плана намеренное и проверяется здесь же: DELIVERED требует ещё и
`tasks_done > 0`. У плана «работы не осталось» — достаточный повод для
`done`; у стори DELIVERED утверждает «поставлено», а стори, все задачи
которой отменены, не поставила ничего.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import (
    Priority,
    StoryLinkKind,
    StoryRelation,
    TaskStatus,
    TaskType,
    UserStoryStatus,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import story_service as stories
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _seed_plan_with_section(session: Session, project_id: int) -> tuple[int, int]:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="X", slug="A-X", position=0)
    session.add(sec)
    session.flush()
    return plan.row_id, sec.row_id


def _make_task(session: Session, project_id: int, plan_id: int, sec_id: int, task_id: str) -> None:
    tasks.create(
        session,
        project_id=project_id,
        plan_id=plan_id,
        section_id=sec_id,
        task_id=task_id,
        title=task_id,
        type=TaskType.FEATURE,
        priority=Priority.LOW,
        author="human:test",
    )


def _link_task(session: Session, story_id: str, task_id: str) -> None:
    stories.link(
        session,
        story_id=story_id,
        to_kind=StoryLinkKind.TASK,
        to_ref=task_id,
        relation=StoryRelation.IMPLEMENTED_BY,
        author="human:test",
    )


def _cancel(session: Session, task_id: str) -> None:
    tasks.update_status(
        session,
        task_id=task_id,
        new_status=TaskStatus.CANCELLED,
        author="human:test",
    )


def test_delivered_when_last_open_task_is_cancelled(engine_with_schema) -> None:
    """Живой симптом: одна задача сделана, вторая отменена — стори поставлена."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        _make_task(session, proj, plan_id, sec_id, "TST-001")
        _make_task(session, proj, plan_id, sec_id, "TST-002")
        story = stories.create(
            session,
            project_id=proj,
            story_id="US-001",
            persona="Owner",
            narrative="As X, I want Y, so Z.",
            priority=Priority.MEDIUM,
            status=UserStoryStatus.ACCEPTED,
            author="human:test",
        )
        _link_task(session, story.story_id, "TST-001")
        _link_task(session, story.story_id, "TST-002")

        tasks.complete(session, task_id="TST-001", author="human:test")
        _cancel(session, "TST-002")

        cov = stories.coverage(session, story.story_id)

    assert cov.status is stories.CoverageStatus.DELIVERED
    assert cov.tasks_done == 1, "cancelled не должен прибавляться к done"
    assert cov.tasks_cancelled == 1
    assert cov.tasks_total == 2


def test_all_tasks_cancelled_is_not_delivered(engine_with_schema) -> None:
    """Стори без единой сделанной задачи не «поставлена», даже если открытых нет."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        _make_task(session, proj, plan_id, sec_id, "TST-010")
        story = stories.create(
            session,
            project_id=proj,
            story_id="US-002",
            persona="Owner",
            narrative="As X, I want Y, so Z.",
            priority=Priority.MEDIUM,
            status=UserStoryStatus.ACCEPTED,
            author="human:test",
        )
        _link_task(session, story.story_id, "TST-010")
        _cancel(session, "TST-010")

        cov = stories.coverage(session, story.story_id)

    assert cov.status is stories.CoverageStatus.ACCEPTED
    assert cov.tasks_cancelled == 1
    assert cov.tasks_done == 0


def test_cancelled_does_not_shortcut_unmet_acceptance(engine_with_schema) -> None:
    """Отмена задачи не закрывает невыполненный критерий приёмки."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        _make_task(session, proj, plan_id, sec_id, "TST-020")
        _make_task(session, proj, plan_id, sec_id, "TST-021")
        story = stories.create(
            session,
            project_id=proj,
            story_id="US-003",
            persona="Owner",
            narrative="As X, I want Y, so Z.",
            priority=Priority.MEDIUM,
            status=UserStoryStatus.ACCEPTED,
            author="human:test",
            acceptance=["A1.", "A2."],
        )
        _link_task(session, story.story_id, "TST-020")
        _link_task(session, story.story_id, "TST-021")

        tasks.complete(session, task_id="TST-020", author="human:test")
        _cancel(session, "TST-021")
        stories.set_criterion_met(
            session, story_id=story.story_id, position=1, met=True, author="human:test"
        )

        cov = stories.coverage(session, story.story_id)

    assert cov.status is stories.CoverageStatus.IN_PROGRESS
    assert cov.acceptance_met == 1
