"""COD-011 / RFL-075: TaskService — create + validation."""

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


def test_create_persists_task_with_pending_status(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s, task_id="MY-001")

        assert task.row_id is not None
        assert task.task_id == "MY-001"
        assert task.status == TaskStatus.PENDING

    with transactional(factory) as session:
        loaded = tasks.get(session, "MY-001")
        assert loaded is not None
        assert loaded.priority == Priority.MEDIUM


def test_create_writes_initial_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)

        assert len(history) == 1
        assert history[0].parent_revision_id is None
        payload = json.loads(history[0].diff)
        assert payload["op"] == "create"


def test_create_auto_generates_task_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        t1 = tasks.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            id_prefix="PLN",
            title="T1",
            type=TaskType.MIGRATION,
            priority=Priority.HIGH,
            author="x",
        )
        t2 = tasks.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            id_prefix="PLN",
            title="T2",
            type=TaskType.MIGRATION,
            priority=Priority.HIGH,
            author="x",
        )
        assert t1.task_id == "PLN-001"
        assert t2.task_id == "PLN-002"


def test_create_without_task_id_and_prefix_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        with pytest.raises(ValueError):
            tasks.create(
                session,
                project_id=p,
                plan_id=pl,
                section_id=s,
                title="X",
                type=TaskType.FEATURE,
                priority=Priority.LOW,
                author="x",
            )


def test_create_rejects_invalid_task_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.services import validation as v

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        with pytest.raises(v.ValidationError) as exc:
            tasks.create(
                session,
                project_id=p,
                plan_id=pl,
                section_id=s,
                task_id="bad",
                title="Test: x",
                type=TaskType.TEST,
                priority=Priority.LOW,
                author="human:test",
            )
        assert exc.value.code == "TP-001"


def test_create_rejects_invalid_id_prefix(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.services import validation as v

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        with pytest.raises(v.ValidationError) as exc:
            tasks.create(
                session,
                project_id=p,
                plan_id=pl,
                section_id=s,
                id_prefix="X",
                title="Implement: foo",
                type=TaskType.FEATURE,
                priority=Priority.LOW,
                author="human:test",
            )
        assert exc.value.code == "TP-002"


def test_create_with_affected_files(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select as _select

    from cod_doc.infra.models import AffectedFileModel

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(
            session,
            p,
            pl,
            s,
            affected_files=["cod_doc/infra/db.py", "tests/infra/test_smoke.py"],
        )
        paths = sorted(
            r[0]
            for r in session.execute(
                _select(AffectedFileModel.path).where(AffectedFileModel.task_id == task.row_id)
            )
        )
        assert paths == ["cod_doc/infra/db.py", "tests/infra/test_smoke.py"]
