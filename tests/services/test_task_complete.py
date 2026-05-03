"""COD-011 / RFL-075: TaskService — complete + list_for_plan."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from cod_doc.domain.entities import EntityKind, Priority, Task, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
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


def test_complete_task_with_no_deps(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)

        done = tasks.complete(
            session,
            task_id=task.task_id,
            author="human:dakh",
            commit_sha="abc1234",
        )
        assert done.status == TaskStatus.DONE
        assert done.completed_at is not None
        assert done.completed_commit == "abc1234"

        history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        diff = json.loads(history[-1].diff)
        assert diff["op"] == "complete"
        assert diff["commit_sha"] == "abc1234"


def test_complete_raises_when_blocking_dep_pending(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """If A blocks B, completing B must fail while A is still pending."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        a = _task(session, p, pl, s, task_id="PLN-001")
        b = _task(session, p, pl, s, task_id="PLN-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()

        with pytest.raises(tasks.TaskBlockedError, match="PLN-001"):
            tasks.complete(session, task_id="PLN-002", author="x")


def test_complete_succeeds_after_blocking_dep_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        a = _task(session, p, pl, s, task_id="PLN-001")
        b = _task(session, p, pl, s, task_id="PLN-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))

        tasks.complete(session, task_id="PLN-001", author="x")
        done_b = tasks.complete(session, task_id="PLN-002", author="x")
        assert done_b.status == TaskStatus.DONE


def test_complete_ignores_non_blocks_kind(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A 'relates' dep to a pending task must NOT block completion."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        a = _task(session, p, pl, s, task_id="PLN-001")
        b = _task(session, p, pl, s, task_id="PLN-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="relates"))
        session.flush()

        done = tasks.complete(session, task_id="PLN-002", author="x")
        assert done.status == TaskStatus.DONE


def test_complete_conflict_via_expected_parent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        first_history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        original_head = first_history[0].revision_id

        tasks.update_status(
            session, task_id=task.task_id, new_status=TaskStatus.IN_PROGRESS, author="other"
        )

        with pytest.raises(rev.RevisionConflictError):
            tasks.complete(
                session,
                task_id=task.task_id,
                author="x",
                expected_parent_revision_id=original_head,
            )


def test_complete_already_done_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        tasks.complete(session, task_id=task.task_id, author="x")

        with pytest.raises(tasks.TaskAlreadyDoneError):
            tasks.complete(session, task_id=task.task_id, author="x")


def test_list_for_plan_returns_all_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        for n in range(1, 4):
            _task(session, p, pl, s, task_id=f"PLN-{n:03d}", title=f"T{n}")
        all_tasks = tasks.list_for_plan(session, pl)
        assert {t.task_id for t in all_tasks} == {"PLN-001", "PLN-002", "PLN-003"}
