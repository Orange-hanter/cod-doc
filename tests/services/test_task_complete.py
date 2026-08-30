"""COD-011 / RFL-075: TaskService — complete + list_for_plan."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, Priority, Task, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
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
            session,
            task_id=task.task_id,
            new_status=TaskStatus.IN_PROGRESS,
            author="other",
            via_checkout=True,
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


# ── ADO-038: complete() идёт через статус-машину ─────────────────────────────


def _force_status(session: Session, task_id: str, status: TaskStatus) -> None:
    """Прямая установка статуса в обход сервиса (подготовка состояния теста)."""
    model = session.execute(select(TaskModel).where(TaskModel.task_id == task_id)).scalar_one()
    model.status = status.value
    session.flush()


@pytest.mark.parametrize("from_status", [TaskStatus.CANCELLED, TaskStatus.BACKLOG])
def test_complete_rejects_terminal_sources(engine_with_schema, from_status) -> None:  # type: ignore[no-untyped-def]
    """cancelled/backlog → done запрещены машиной — complete() должен резать."""
    from cod_doc.services.task_status_machine import StatusTransitionError

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        _force_status(session, task.task_id, from_status)

        with pytest.raises(StatusTransitionError):
            tasks.complete(session, task_id=task.task_id, author="x")

        rejected = tasks.get(session, task.task_id)
        assert rejected is not None
        assert rejected.status is from_status


@pytest.mark.parametrize(
    "from_status",
    [TaskStatus.IN_PROGRESS, TaskStatus.IN_PROGRESS_NEW, TaskStatus.IN_REVIEW],
)
def test_complete_allows_legal_sources(engine_with_schema, from_status) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        _force_status(session, task.task_id, from_status)

        tasks.complete(session, task_id=task.task_id, author="x")
        done = tasks.get(session, task.task_id)
        assert done is not None
        assert done.status is TaskStatus.DONE


@pytest.mark.parametrize("from_status", [TaskStatus.PENDING, TaskStatus.TODO])
def test_complete_from_todo_goes_via_checkout_leg(engine_with_schema, from_status) -> None:  # type: ignore[no-untyped-def]
    """todo→done в машине нет — complete() делает явную ногу todo→in_progress."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        _force_status(session, task.task_id, from_status)

        tasks.complete(session, task_id=task.task_id, author="x")

        done = tasks.get(session, task.task_id)
        assert done is not None
        assert done.status is TaskStatus.DONE
        history = rev.list_for_entity(session, EntityKind.TASK, done.row_id)  # type: ignore[arg-type]
        ops = [json.loads(r.diff)["op"] for r in history]
        # create → status(todo→in-progress, checkout-нога) → complete
        assert ops == ["create", "status", "complete"]
