"""SYM-005B migration 0027: composite UNIQUE(project_id, task_id) on task."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

BEFORE = "0026_document_type_recoercion"


def _alembic(db_url: str, *args: str) -> None:
    run_alembic(*args, db_url=db_url)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'shared_hub.db'}"


def _insert_row(session: Session, table: str, **values: object) -> int:
    """Строка сырым SQL — только колонки, которые есть на старой ревизии.

    ORM-модель знает колонки поздних ревизий (``plan.id_prefix`` из 0043,
    ``uid`` из 0049) и вставляет их как NULL даже незаданными; в схеме 0026
    их ещё нет. ``table`` — литерал из этого модуля.
    """
    cols = ", ".join(values)
    marks = ", ".join(f":{c}" for c in values)
    return int(
        session.execute(
            text(f"INSERT INTO {table} ({cols}) VALUES ({marks}) RETURNING row_id"), values
        ).scalar_one()
    )


def _plan_id(session: Session, project_id: int) -> int:
    """``row_id`` плана проекта — только колонка, а не вся ORM-строка."""
    return int(
        session.execute(
            select(PlanModel.row_id).where(PlanModel.project_id == project_id)
        ).scalar_one()
    )


def _seed_pre_0027(db_url: str) -> tuple[int, int, int, int, int, int]:
    """Populate a 0026-era DB with two projects, tasks, deps, affected files."""
    _alembic(db_url, "upgrade", BEFORE)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)

    try:
        with transactional(factory) as session:
            proj_a, proj_b = (
                _insert_row(
                    session,
                    "project",
                    slug=slug,
                    title=slug.upper(),
                    root_path=f"/tmp/{slug}",
                    config_json="{}",
                    created=now,
                    updated=now,
                )
                for slug in ("a", "b")
            )
            plan_a_id, plan_b_id = (
                _insert_row(
                    session, "plan", project_id=pid, scope=scope, created=now, last_updated=now
                )
                for pid, scope in ((proj_a, "a-plan"), (proj_b, "b-plan"))
            )
            sec_a, sec_b = (
                _insert_row(
                    session,
                    "plan_section",
                    plan_id=plan_id,
                    letter="A",
                    title=title,
                    slug=slug,
                    position=0,
                )
                for plan_id, title, slug in (
                    (plan_a_id, "Section A", "A-sec"),
                    (plan_b_id, "Section B", "B-sec"),
                )
            )
            t_a1, t_a2, t_b1 = (
                _insert_row(
                    session,
                    "task",
                    project_id=pid,
                    task_id=task_id,
                    plan_id=plan_id,
                    section_id=sec_id,
                    title=title,
                    status=status,
                    type=type_,
                    priority=priority,
                    created=now,
                    last_updated=now,
                )
                for pid, task_id, plan_id, sec_id, title, status, type_, priority in (
                    (
                        proj_a,
                        "TASK-001",
                        plan_a_id,
                        sec_a,
                        "A first",
                        "pending",
                        "feature",
                        "medium",
                    ),
                    (proj_a, "TASK-002", plan_a_id, sec_a, "A second", "done", "bug", "high"),
                    (proj_b, "B-001", plan_b_id, sec_b, "B first", "pending", "feature", "low"),
                )
            )
            _insert_row(session, "dependency", from_task_id=t_a1, to_task_id=t_a2, kind="blocks")
            _insert_row(session, "affected_file", task_id=t_a1, path="a/foo.py", kind="source")
            _insert_row(session, "affected_file", task_id=t_b1, path="b/bar.py", kind="test")
            return proj_a, proj_b, t_a1, t_a2, t_b1, sec_a
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
            plan_b_id = _plan_id(session, proj_b.row_id)
            sec_b = session.query(PlanSectionModel).filter_by(plan_id=plan_b_id).one()
            duplicate = TaskModel(
                project_id=proj_b.row_id,
                task_id="TASK-001",  # same as a project A task, different project
                plan_id=plan_b_id,
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
            plan_a_id = _plan_id(session, proj_a.row_id)
            sec_a = session.query(PlanSectionModel).filter_by(plan_id=plan_a_id).one()
            duplicate = TaskModel(
                project_id=proj_a.row_id,
                task_id="TASK-001",  # already exists in project A
                plan_id=plan_a_id,
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
            plan_b_id = _plan_id(session, proj_b.row_id)
            sec_b = session.query(PlanSectionModel).filter_by(plan_id=plan_b_id).one()
            session.add(
                TaskModel(
                    project_id=proj_b.row_id,
                    task_id="TASK-001",  # collides with project A
                    plan_id=plan_b_id,
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
