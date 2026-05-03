"""COD-011 / RFL-075: TaskService — update_status."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from cod_doc.domain.entities import EntityKind, Priority, Task, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_plan(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Data Core", slug="A-Data-Core", position=0
    )
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _task(
    session: Session,
    proj_id: int,
    plan_id: int,
    sec_id: int,
    task_id: str = "PLN-001",
    title: str = "Task",
    **kw: Any,
) -> Task:
    return tasks.create(
        session,
        project_id=proj_id,
        plan_id=plan_id,
        section_id=sec_id,
        task_id=task_id,
        title=title,
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:dakh",
        **kw,
    )


def test_update_status_pending_to_in_progress(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)

        updated = tasks.update_status(
            session,
            task_id=task.task_id,
            new_status=TaskStatus.IN_PROGRESS,
            author="human:dakh",
        )
        assert updated.status == TaskStatus.IN_PROGRESS

        history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        assert len(history) == 2
        diff = json.loads(history[1].diff)
        assert diff["op"] == "status"
        assert diff["new"] == "in-progress"


def test_update_status_no_op_if_same(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        tasks.update_status(
            session, task_id=task.task_id, new_status=TaskStatus.PENDING, author="x"
        )
        history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        assert len(history) == 1


def test_update_status_unknown_task_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session, pytest.raises(tasks.TaskNotFoundError):
        tasks.update_status(
            session, task_id="GHOST-001", new_status=TaskStatus.IN_PROGRESS, author="x"
        )


def test_update_status_concurrency_conflict(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        head = rev.list_for_entity(session, EntityKind.TASK, task.row_id)[0].revision_id

        tasks.update_status(
            session, task_id=task.task_id, new_status=TaskStatus.IN_PROGRESS, author="other"
        )

        with pytest.raises(rev.RevisionConflictError):
            tasks.update_status(
                session,
                task_id=task.task_id,
                new_status=TaskStatus.DONE,
                author="x",
                expected_parent_revision_id=head,
            )
