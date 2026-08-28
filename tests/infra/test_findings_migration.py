"""SYM-005C migration 0028: findings tables + FTS scope."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    ExternalRefModel,
    FindingModel,
    FindingSourceRunModel,
    ProjectModel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0027_shared_hub"


def _alembic(db_url: str, *args: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", *args]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'findings.db'}"


def _seed_projects(db_url: str) -> tuple[int, int]:
    """Create two projects for findings tests."""
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
            return proj_a.row_id, proj_b.row_id
    finally:
        engine.dispose()


@pytest.fixture
def seeded(db_url: str) -> str:
    _alembic(db_url, "upgrade", BEFORE)
    _seed_projects(db_url)
    return db_url


def test_upgrade_downgrade_upgrade_is_symmetric(seeded: str) -> None:
    """head → -1 → head is error-free and leaves expected views intact."""
    _alembic(seeded, "upgrade", "head")
    _alembic(seeded, "downgrade", "-1")
    _alembic(seeded, "upgrade", "head")

    engine = make_engine(seeded)
    try:
        with engine.connect() as conn:
            views = {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='view'"))
            }
            assert {"section_totals", "plan_totals", "ready_tasks"} <= views
    finally:
        engine.dispose()


def test_upgrade_rejects_duplicate_fingerprint_within_project(seeded: str) -> None:
    """UNIQUE(project_id, source, fingerprint) rejects a duplicate in one project."""
    _alembic(seeded, "upgrade", "head")
    engine = make_engine(seeded)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)

    try:
        with transactional(factory) as session:
            proj_a = session.query(ProjectModel).filter_by(slug="a").one()
            session.add(
                FindingModel(
                    project_id=proj_a.row_id,
                    finding_uid="uid-001",
                    source="ai_review",
                    fingerprint="fp-001",
                    severity="major",
                    title="First finding",
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )

        with (
            pytest.raises(IntegrityError),
            transactional(factory) as session,
        ):
            proj_a = session.query(ProjectModel).filter_by(slug="a").one()
            session.add(
                FindingModel(
                    project_id=proj_a.row_id,
                    finding_uid="uid-002",
                    source="ai_review",
                    fingerprint="fp-001",
                    severity="minor",
                    title="Duplicate fingerprint",
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
    finally:
        engine.dispose()


def test_upgrade_allows_same_fingerprint_across_projects(seeded: str) -> None:
    """The same fingerprint in a different project is allowed."""
    _alembic(seeded, "upgrade", "head")
    engine = make_engine(seeded)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)

    try:
        with transactional(factory) as session:
            proj_a = session.query(ProjectModel).filter_by(slug="a").one()
            proj_b = session.query(ProjectModel).filter_by(slug="b").one()
            session.add(
                FindingModel(
                    project_id=proj_a.row_id,
                    finding_uid="uid-a",
                    source="ai_review",
                    fingerprint="shared-fp",
                    severity="major",
                    title="Finding in A",
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            session.add(
                FindingModel(
                    project_id=proj_b.row_id,
                    finding_uid="uid-b",
                    source="ai_review",
                    fingerprint="shared-fp",
                    severity="major",
                    title="Finding in B",
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM finding WHERE fingerprint = 'shared-fp'")
            ).scalar_one()
            assert int(count) == 2
    finally:
        engine.dispose()


def test_finding_source_run_cascades_on_delete(seeded: str) -> None:
    """Deleting a finding removes its source-run rows via ON DELETE CASCADE."""
    _alembic(seeded, "upgrade", "head")
    engine = make_engine(seeded)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)

    try:
        with transactional(factory) as session:
            proj_a = session.query(ProjectModel).filter_by(slug="a").one()
            finding = FindingModel(
                project_id=proj_a.row_id,
                finding_uid="uid-cascade",
                source="zairgrush",
                fingerprint="fp-cascade",
                severity="info",
                title="Cascade test",
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(finding)
            session.flush()
            session.add(
                FindingSourceRunModel(
                    finding_id=finding.row_id,
                    source_run_id="run-1",
                    ts=now,
                    raw={"version": 1},
                )
            )
            session.flush()
            session.delete(finding)

        with engine.connect() as conn:
            remaining = conn.execute(
                text("SELECT COUNT(*) FROM finding_source_run WHERE source_run_id = 'run-1'")
            ).scalar_one()
            assert int(remaining) == 0
    finally:
        engine.dispose()


def test_external_ref_unique_constraint(seeded: str) -> None:
    """External ref enforces UNIQUE(project_id, system, external_id)."""
    _alembic(seeded, "upgrade", "head")
    engine = make_engine(seeded)
    factory = make_session_factory(engine)

    try:
        with transactional(factory) as session:
            proj_a = session.query(ProjectModel).filter_by(slug="a").one()
            session.add(
                ExternalRefModel(
                    project_id=proj_a.row_id,
                    entity_kind="task",
                    entity_row_id=1,
                    system="zairgrush",
                    external_id="ZRG-001",
                )
            )

        with (
            pytest.raises(IntegrityError),
            transactional(factory) as session,
        ):
            proj_a = session.query(ProjectModel).filter_by(slug="a").one()
            session.add(
                ExternalRefModel(
                    project_id=proj_a.row_id,
                    entity_kind="task",
                    entity_row_id=2,
                    system="zairgrush",
                    external_id="ZRG-001",
                )
            )
    finally:
        engine.dispose()
