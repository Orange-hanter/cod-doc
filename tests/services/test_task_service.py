"""COD-011: TaskService — create / update_status / complete."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from cod_doc.domain.entities import EntityKind, Priority, Task, TaskStatus, TaskType
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'tasks.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed_plan(session: Session) -> tuple[int, int, int]:
    """Seed project + plan + one section. Returns (project_id, plan_id, section_id)."""
    now = datetime.now(timezone.utc)
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
    task_id: str = "P-001",
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


# ----------------------------- create ----------------------------------------


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
            session, project_id=p, plan_id=pl, section_id=s,
            id_prefix="P", title="T1", type=TaskType.MIGRATION,
            priority=Priority.HIGH, author="x",
        )
        t2 = tasks.create(
            session, project_id=p, plan_id=pl, section_id=s,
            id_prefix="P", title="T2", type=TaskType.MIGRATION,
            priority=Priority.HIGH, author="x",
        )
        assert t1.task_id == "P-001"
        assert t2.task_id == "P-002"


def test_create_without_task_id_and_prefix_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        with pytest.raises(ValueError):
            tasks.create(
                session, project_id=p, plan_id=pl, section_id=s,
                title="X", type=TaskType.FEATURE, priority=Priority.LOW, author="x",
            )


def test_create_with_affected_files(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select as _select  # noqa: PLC0415

    from cod_doc.infra.models import AffectedFileModel  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(
            session, p, pl, s,
            affected_files=["cod_doc/infra/db.py", "tests/infra/test_smoke.py"],
        )
        paths = sorted(
            r[0]
            for r in session.execute(
                _select(AffectedFileModel.path).where(
                    AffectedFileModel.task_id == task.row_id
                )
            )
        )
        assert paths == ["cod_doc/infra/db.py", "tests/infra/test_smoke.py"]


# ----------------------------- update_status ---------------------------------


def test_update_status_pending_to_in_progress(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)

        updated = tasks.update_status(
            session, task_id=task.task_id,
            new_status=TaskStatus.IN_PROGRESS, author="human:dakh",
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
        tasks.update_status(session, task_id=task.task_id, new_status=TaskStatus.PENDING, author="x")
        history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        assert len(history) == 1  # no new revision


def test_update_status_unknown_task_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        with pytest.raises(tasks.TaskNotFoundError):
            tasks.update_status(
                session, task_id="GHOST-001", new_status=TaskStatus.IN_PROGRESS, author="x"
            )


# ----------------------------- complete --------------------------------------


def test_complete_task_with_no_deps(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)

        done = tasks.complete(
            session, task_id=task.task_id, author="human:dakh",
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
        a = _task(session, p, pl, s, task_id="P-001")
        b = _task(session, p, pl, s, task_id="P-002")
        # B depends on A (from_task=B, to_task=A)
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()

        with pytest.raises(tasks.TaskBlockedError, match="P-001"):
            tasks.complete(session, task_id="P-002", author="x")


def test_complete_succeeds_after_blocking_dep_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        a = _task(session, p, pl, s, task_id="P-001")
        b = _task(session, p, pl, s, task_id="P-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))

        # Complete A first.
        tasks.complete(session, task_id="P-001", author="x")
        # Now B can be completed.
        done_b = tasks.complete(session, task_id="P-002", author="x")
        assert done_b.status == TaskStatus.DONE


def test_complete_ignores_non_blocks_kind(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A 'relates' dep to a pending task must NOT block completion."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        a = _task(session, p, pl, s, task_id="P-001")
        b = _task(session, p, pl, s, task_id="P-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="relates"))
        session.flush()

        done = tasks.complete(session, task_id="P-002", author="x")
        assert done.status == TaskStatus.DONE


def test_complete_conflict_via_expected_parent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        first_history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        original_head = first_history[0].revision_id

        # A concurrent update lands first.
        tasks.update_status(session, task_id=task.task_id, new_status=TaskStatus.IN_PROGRESS, author="other")

        # Now complete with stale expected_parent must conflict.
        with pytest.raises(rev.RevisionConflictError):
            tasks.complete(
                session, task_id=task.task_id, author="x",
                expected_parent_revision_id=original_head,
            )


# ----------------------------- list_for_plan ---------------------------------


def test_list_for_plan_returns_all_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        for n in range(1, 4):
            _task(session, p, pl, s, task_id=f"P-{n:03d}", title=f"T{n}")
        all_tasks = tasks.list_for_plan(session, pl)
        assert {t.task_id for t in all_tasks} == {"P-001", "P-002", "P-003"}


# ------- SB-ME-2: update_status optimistic concurrency ----------------------


def test_update_status_concurrency_conflict(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        head = rev.list_for_entity(session, EntityKind.TASK, task.row_id)[0].revision_id

        # Concurrent writer lands first.
        tasks.update_status(session, task_id=task.task_id, new_status=TaskStatus.IN_PROGRESS, author="other")

        # We still hold the stale head → must conflict.
        with pytest.raises(rev.RevisionConflictError):
            tasks.update_status(
                session, task_id=task.task_id, new_status=TaskStatus.DONE,
                author="x", expected_parent_revision_id=head,
            )


# ------- SB-LO-6: double-complete guard --------------------------------------


def test_complete_already_done_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        tasks.complete(session, task_id=task.task_id, author="x")

        with pytest.raises(tasks.TaskAlreadyDoneError):
            tasks.complete(session, task_id=task.task_id, author="x")
