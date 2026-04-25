"""COD-002 smoke: tasks schema + section_totals / plan_totals / ready_tasks views."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)

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


def _seed_plan_with_tasks(session, statuses: list[str]) -> tuple[int, int, list[int]]:
    """Seed one project / one plan / one section / N tasks with given statuses.

    Returns (plan_id, section_id, [task_row_id, ...]).
    """
    now = datetime.now(timezone.utc)
    project = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    project.created = now
    project.updated = now
    session.add(project)
    session.flush()

    plan = PlanModel(
        project_id=project.row_id,
        scope="p-plan",
        principle="test-first",
        created=now,
        last_updated=now,
    )
    session.add(plan)
    session.flush()

    section = PlanSectionModel(
        plan_id=plan.row_id,
        letter="A",
        title="Data Core",
        slug="A-Data-Core",
        position=0,
    )
    session.add(section)
    session.flush()

    task_ids: list[int] = []
    for idx, status in enumerate(statuses):
        task = TaskModel(
            project_id=project.row_id,
            task_id=f"P-{idx:03d}",
            plan_id=plan.row_id,
            section_id=section.row_id,
            title=f"Task {idx}",
            status=status,
            type="feature",
            priority="medium",
            created=now,
            last_updated=now,
        )
        session.add(task)
        session.flush()
        task_ids.append(task.row_id)

    return plan.row_id, section.row_id, task_ids


def test_migration_creates_tables_and_views(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    with engine_with_schema.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        views = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='view'")
            )
        }
    assert {"plan", "plan_section", "task", "dependency", "affected_file"} <= tables
    assert {"section_totals", "plan_totals", "ready_tasks"} <= views


def test_section_and_plan_totals_aggregate_correctly(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        plan_id, section_id, _ = _seed_plan_with_tasks(
            session, ["pending", "pending", "in-progress", "done", "done", "done"]
        )

    with engine_with_schema.connect() as conn:
        sec_row = conn.execute(
            text(
                "SELECT tasks_total, tasks_done, tasks_in_progress "
                "FROM section_totals WHERE section_id = :sid"
            ),
            {"sid": section_id},
        ).one()
        plan_row = conn.execute(
            text(
                "SELECT tasks_total, tasks_done, tasks_in_progress "
                "FROM plan_totals WHERE plan_id = :pid"
            ),
            {"pid": plan_id},
        ).one()

    assert tuple(sec_row) == (6, 3, 1)
    assert tuple(plan_row) == (6, 3, 1)


def test_section_totals_zero_for_empty_section(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """LEFT JOIN must yield (0, 0, 0) — not NULL — for a section without tasks."""
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        proj = ProjectModel(slug="empty", title="Empty", root_path="/tmp/empty", config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()
        plan = PlanModel(
            project_id=proj.row_id, scope="empty-plan", created=now, last_updated=now
        )
        session.add(plan)
        session.flush()
        sec = PlanSectionModel(
            plan_id=plan.row_id, letter="A", title="Empty", slug="A-Empty", position=0
        )
        session.add(sec)
        session.flush()
        sec_id = sec.row_id

    with engine_with_schema.connect() as conn:
        row = conn.execute(
            text(
                "SELECT tasks_total, tasks_done, tasks_in_progress "
                "FROM section_totals WHERE section_id = :sid"
            ),
            {"sid": sec_id},
        ).one()
    assert tuple(row) == (0, 0, 0)


def test_ready_tasks_excludes_blocked_pending(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ready_tasks: pending tasks whose blocks-deps are all done."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        # 4 pending tasks: T0, T1, T2, T3
        _, _, [t0, t1, t2, t3] = _seed_plan_with_tasks(
            session, ["pending", "pending", "pending", "pending"]
        )
        # T1 blocks T0 → T0 not ready until T1 is done
        session.add(DependencyModel(from_task_id=t0, to_task_id=t1, kind="blocks"))
        # T3 blocks T2, but mark T3 done → T2 ready
        session.add(DependencyModel(from_task_id=t2, to_task_id=t3, kind="blocks"))
        session.flush()
        session.get(TaskModel, t3).status = "done"

    with engine_with_schema.connect() as conn:
        ready_ids = {
            r[0]
            for r in conn.execute(text("SELECT row_id FROM ready_tasks"))
        }

    # T0 blocked by pending T1 → excluded
    # T1 has no deps → ready
    # T2 blocked by T3 (done) → ready
    # T3 not pending (done) → excluded
    assert ready_ids == {t1, t2}


def test_ready_tasks_ignores_non_blocks_kind(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A 'relates' edge to a pending task does NOT block readiness."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _, _, [t0, t1] = _seed_plan_with_tasks(session, ["pending", "pending"])
        session.add(DependencyModel(from_task_id=t0, to_task_id=t1, kind="relates"))

    with engine_with_schema.connect() as conn:
        ready_ids = {
            r[0]
            for r in conn.execute(text("SELECT row_id FROM ready_tasks"))
        }
    assert ready_ids == {t0, t1}


def test_dependency_unique_edge(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Same (from, to, kind) edge cannot be inserted twice."""
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _, _, [a, b] = _seed_plan_with_tasks(session, ["pending", "pending"])
        session.add(DependencyModel(from_task_id=a, to_task_id=b, kind="blocks"))

    with pytest.raises(IntegrityError):
        with transactional(factory) as session:
            session.add(DependencyModel(from_task_id=a, to_task_id=b, kind="blocks"))
