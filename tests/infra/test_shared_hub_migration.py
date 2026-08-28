"""SYM-005B migration 0027: composite UNIQUE(project_id, task_id) on task."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    AffectedFileModel,
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0026_document_type_recoercion"


def _alembic(db_url: str, *args: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", *args]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'shared_hub.db'}"


def _seed_pre_0027(db_url: str) -> tuple[int, int, int, int, int, int]:
    """Populate a 0026-era DB with two projects, tasks, deps, affected files."""
    _alembic(db_url, "upgrade", BEFORE)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)

    try:
        with transactional(factory) as session:
            proj_a = ProjectModel(slug="a", title="A", root_path="/tmp/a", config_json={})
            proj_b = ProjectModel(slug="b", title="B", root_path="/tmp/b", config_json={})
            proj_a.created = now
            proj_a.updated = now
            proj_b.created = now
            proj_b.updated = now
            session.add_all([proj_a, proj_b])
            session.flush()

            plan_a = PlanModel(
                project_id=proj_a.row_id,
                scope="a-plan",
                created=now,
                last_updated=now,
            )
            plan_b = PlanModel(
                project_id=proj_b.row_id,
                scope="b-plan",
                created=now,
                last_updated=now,
            )
            session.add_all([plan_a, plan_b])
            session.flush()

            sec_a = PlanSectionModel(
                plan_id=plan_a.row_id,
                letter="A",
                title="Section A",
                slug="A-sec",
                position=0,
            )
            sec_b = PlanSectionModel(
                plan_id=plan_b.row_id,
                letter="A",
                title="Section B",
                slug="B-sec",
                position=0,
            )
            session.add_all([sec_a, sec_b])
            session.flush()

            t_a1 = TaskModel(
                project_id=proj_a.row_id,
                task_id="TASK-001",
                plan_id=plan_a.row_id,
                section_id=sec_a.row_id,
                title="A first",
                status="pending",
                type="feature",
                priority="medium",
                created=now,
                last_updated=now,
            )
            t_a2 = TaskModel(
                project_id=proj_a.row_id,
                task_id="TASK-002",
                plan_id=plan_a.row_id,
                section_id=sec_a.row_id,
                title="A second",
                status="done",
                type="bug",
                priority="high",
                created=now,
                last_updated=now,
            )
            t_b1 = TaskModel(
                project_id=proj_b.row_id,
                task_id="B-001",
                plan_id=plan_b.row_id,
                section_id=sec_b.row_id,
                title="B first",
                status="pending",
                type="feature",
                priority="low",
                created=now,
                last_updated=now,
            )
            session.add_all([t_a1, t_a2, t_b1])
            session.flush()

            session.add(
                DependencyModel(from_task_id=t_a1.row_id, to_task_id=t_a2.row_id, kind="blocks")
            )
            session.add(AffectedFileModel(task_id=t_a1.row_id, path="a/foo.py", kind="source"))
            session.add(AffectedFileModel(task_id=t_b1.row_id, path="b/bar.py", kind="test"))
            session.flush()

            return (
                proj_a.row_id,
                proj_b.row_id,
                t_a1.row_id,
                t_a2.row_id,
                t_b1.row_id,
                sec_a.row_id,
            )
    finally:
        engine.dispose()


@pytest.fixture
def seeded_pre_0027(db_url: str) -> str:
    _seed_pre_0027(db_url)
    return db_url


def test_upgrade_preserves_rows_and_fks(seeded_pre_0027: str) -> None:
    """After upgrade all task rows, dependencies, affected files, and FKs survive."""
    _alembic(seeded_pre_0027, "upgrade", "head")
    engine = make_engine(seeded_pre_0027)
    try:
        with engine.connect() as conn:
            fk_errors = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
            assert fk_errors == []

            counts = dict(
                conn.execute(
                    text(
                        "SELECT name, (SELECT COUNT(*) FROM task) FROM sqlite_master "
                        "WHERE type='table' AND name='task'"
                    )
                ).fetchall()
            )
            assert counts["task"] == 3

            deps = conn.execute(text("SELECT from_task_id, to_task_id FROM dependency")).fetchall()
            assert len(deps) == 1

            files = conn.execute(
                text("SELECT task_id, path FROM affected_file ORDER BY path")
            ).fetchall()
            assert len(files) == 2
            assert files[0].path == "a/foo.py"
            assert files[1].path == "b/bar.py"
    finally:
        engine.dispose()


def test_upgrade_allows_same_task_id_across_projects(seeded_pre_0027: str) -> None:
    """UNIQUE(project_id, task_id) permits the same task_id in different projects."""
    _alembic(seeded_pre_0027, "upgrade", "head")
    engine = make_engine(seeded_pre_0027)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)

    try:
        with transactional(factory) as session:
            proj_b = session.query(ProjectModel).filter_by(slug="b").one()
            plan_b = session.query(PlanModel).filter_by(project_id=proj_b.row_id).one()
            sec_b = session.query(PlanSectionModel).filter_by(plan_id=plan_b.row_id).one()
            duplicate = TaskModel(
                project_id=proj_b.row_id,
                task_id="TASK-001",  # same as a project A task, different project
                plan_id=plan_b.row_id,
                section_id=sec_b.row_id,
                title="B duplicate of A-001",
                status="pending",
                type="feature",
                priority="medium",
                created=now,
                last_updated=now,
            )
            session.add(duplicate)

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) FROM task WHERE task_id = 'TASK-001'")
            ).scalar()
            assert row == 2
    finally:
        engine.dispose()


def test_upgrade_rejects_same_task_id_within_project(seeded_pre_0027: str) -> None:
    """UNIQUE(project_id, task_id) rejects a duplicate task_id inside one project."""
    _alembic(seeded_pre_0027, "upgrade", "head")
    engine = make_engine(seeded_pre_0027)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)

    try:
        with (
            pytest.raises(IntegrityError),
            transactional(factory) as session,
        ):
            proj_a = session.query(ProjectModel).filter_by(slug="a").one()
            plan_a = session.query(PlanModel).filter_by(project_id=proj_a.row_id).one()
            sec_a = session.query(PlanSectionModel).filter_by(plan_id=plan_a.row_id).one()
            duplicate = TaskModel(
                project_id=proj_a.row_id,
                task_id="TASK-001",  # already exists in project A
                plan_id=plan_a.row_id,
                section_id=sec_a.row_id,
                title="A duplicate",
                status="pending",
                type="feature",
                priority="medium",
                created=now,
                last_updated=now,
            )
            session.add(duplicate)
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_is_symmetric(db_url: str) -> None:
    """head → 0026 → head is error-free on a fresh database."""
    _alembic(db_url, "upgrade", "head")
    _alembic(db_url, "downgrade", BEFORE)
    _alembic(db_url, "upgrade", "head")

    engine = make_engine(db_url)
    try:
        with engine.connect() as conn:
            views = {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='view'"))
            }
            assert {"section_totals", "plan_totals", "ready_tasks"} <= views
    finally:
        engine.dispose()


def test_downgrade_fails_when_task_id_is_shared_across_projects(seeded_pre_0027: str) -> None:
    """Reverting to UNIQUE(task_id) is impossible without data loss if IDs collide."""
    _alembic(seeded_pre_0027, "upgrade", "head")
    engine = make_engine(seeded_pre_0027)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)

    try:
        with transactional(factory) as session:
            proj_b = session.query(ProjectModel).filter_by(slug="b").one()
            plan_b = session.query(PlanModel).filter_by(project_id=proj_b.row_id).one()
            sec_b = session.query(PlanSectionModel).filter_by(plan_id=plan_b.row_id).one()
            session.add(
                TaskModel(
                    project_id=proj_b.row_id,
                    task_id="TASK-001",  # collides with project A
                    plan_id=plan_b.row_id,
                    section_id=sec_b.row_id,
                    title="B collides",
                    status="pending",
                    type="feature",
                    priority="medium",
                    created=now,
                    last_updated=now,
                )
            )

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            _alembic(seeded_pre_0027, "downgrade", BEFORE)
        assert "Cannot downgrade 0027_shared_hub" in exc_info.value.stderr.decode()
    finally:
        engine.dispose()
