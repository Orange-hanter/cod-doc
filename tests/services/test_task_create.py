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


# --------------------------------------------------------------------------- #
# PCA-902 / PCA-903: blocked_by + story_id persistence (cycle-2 G2/G3 close)   #
# --------------------------------------------------------------------------- #


def test_create_with_blocked_by_persists_dependency_rows(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """blocked_by → dependency rows (kind='blocks', from=new → to=blocker)."""
    from sqlalchemy import select as _select

    from cod_doc.infra.models import DependencyModel

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        a = _task(session, p, pl, s, task_id="MY-001", title="Task A")
        b = _task(session, p, pl, s, task_id="MY-002", title="Task B")
        c = _task(
            session,
            p,
            pl,
            s,
            task_id="MY-003",
            title="Task C",
            blocked_by=["MY-001", "MY-002"],
        )

        deps = sorted(
            (d.from_task_id, d.to_task_id, d.kind)
            for d in session.execute(_select(DependencyModel)).scalars()
        )
        assert deps == [
            (c.row_id, a.row_id, "blocks"),
            (c.row_id, b.row_id, "blocks"),
        ]


def test_create_with_unknown_blocked_by_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        with pytest.raises(ValueError) as exc:
            _task(
                session,
                p,
                pl,
                s,
                task_id="MY-001",
                title="orphan blocker",
                blocked_by=["DOES-999"],
            )
        assert "DOES-999" in str(exc.value)


def test_create_with_story_id_creates_story_link(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """story_id → story_link row (story → to_kind='task', relation='implemented_by')."""
    from sqlalchemy import select as _select

    from cod_doc.domain.entities import UserStory, UserStoryStatus
    from cod_doc.infra.models import StoryLinkModel
    from cod_doc.infra.repositories import UserStoryRepository

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        story = UserStoryRepository(session).add(
            UserStory(
                project_id=p,
                story_id="US-100",
                persona="dev",
                narrative="хочу X",
                status=UserStoryStatus.DRAFT,
                priority=Priority.MEDIUM,
            )
        )
        task = _task(
            session,
            p,
            pl,
            s,
            task_id="MY-001",
            title="Implement X",
            story_id="US-100",
        )

        links = list(
            session.execute(
                _select(StoryLinkModel).where(StoryLinkModel.story_id == story.row_id)
            ).scalars()
        )
        assert len(links) == 1
        link = links[0]
        assert link.to_kind == "task"
        assert link.to_ref == task.task_id
        assert link.relation == "implemented_by"


def test_create_with_unknown_story_id_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        with pytest.raises(ValueError) as exc:
            _task(
                session,
                p,
                pl,
                s,
                task_id="MY-001",
                title="ghost story",
                story_id="US-DOES-NOT-EXIST",
            )
        assert "US-DOES-NOT-EXIST" in str(exc.value)


def test_plan_ready_excludes_tasks_with_unfinished_blockers(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """After PCA-902 fix: plan.ready honours dependency edges from blocked_by."""
    from cod_doc.services import plan_service

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _task(session, p, pl, s, task_id="MY-001", title="A")
        _task(
            session,
            p,
            pl,
            s,
            task_id="MY-002",
            title="B blocked by A",
            blocked_by=["MY-001"],
        )

        ready = plan_service.ready(session, pl)
        ready_ids = {t.task_id for t in ready}
        assert "MY-001" in ready_ids
        assert "MY-002" not in ready_ids
