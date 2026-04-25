"""COD-004 smoke: revision (§3.5) + audit_log (§3.13)."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from ulid import ULID

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    AuditLogModel,
    ProjectModel,
    RevisionModel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'revisions.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _add_project(session, slug: str = "p") -> int:
    now = datetime.now(timezone.utc)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_migration_creates_tables_and_indexes(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    with engine_with_schema.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        indexes = {
            r[0]
            for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
        }
    assert {"revision", "audit_log"} <= tables
    assert {
        "ix_revision_entity",
        "ix_revision_parent",
        "ix_audit_action",
        "ix_audit_actor",
    } <= indexes


def test_revision_chain_for_one_entity(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Two revisions for the same task: ULIDs sort by time; parent_revision_id chains them."""
    factory = make_session_factory(engine_with_schema)

    base = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    rid1 = str(ULID())
    rid2 = str(ULID())

    with transactional(factory) as session:
        proj_id = _add_project(session, "rchain")
        session.add(
            RevisionModel(
                revision_id=rid1,
                project_id=proj_id,
                entity_kind="task",
                entity_id=42,
                parent_revision_id=None,
                author="agent:task-steward",
                at=base,
                diff="--- a\n+++ b\n@@ ...",
                reason="initial",
            )
        )
        session.add(
            RevisionModel(
                revision_id=rid2,
                project_id=proj_id,
                entity_kind="task",
                entity_id=42,
                parent_revision_id=rid1,
                author="human:dakh",
                at=base + timedelta(minutes=5),
                diff="--- b\n+++ c\n@@ ...",
                reason="status->done",
                commit_sha="deadbeef",
            )
        )

    with transactional(factory) as session:
        history = session.execute(
            select(RevisionModel)
            .where(RevisionModel.entity_kind == "task", RevisionModel.entity_id == 42)
            .order_by(RevisionModel.at)
        ).scalars().all()

        assert [r.revision_id for r in history] == [rid1, rid2]
        assert history[0].parent_revision_id is None
        assert history[1].parent_revision_id == rid1
        assert history[1].commit_sha == "deadbeef"


def test_revision_id_is_unique(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)
    rid = str(ULID())
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        proj_id = _add_project(session, "ruq")
        session.add(
            RevisionModel(
                revision_id=rid,
                project_id=proj_id,
                entity_kind="document",
                entity_id=1,
                author="x",
                at=now,
                diff="d",
            )
        )

    with pytest.raises(IntegrityError):
        with transactional(factory) as session:
            proj_id2 = _add_project(session, "ruq2")
            session.add(
                RevisionModel(
                    revision_id=rid,  # same ULID — must collide globally
                    project_id=proj_id2,
                    entity_kind="document",
                    entity_id=2,
                    author="y",
                    at=now,
                    diff="d2",
                )
            )


def test_audit_log_persists_payload_as_json(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """payload_json round-trips as a real dict, not stringified."""
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        proj_id = _add_project(session, "audit")
        session.add(
            AuditLogModel(
                project_id=proj_id,
                actor="agent:task-steward",
                surface="mcp",
                action="task.create",
                payload_json={"task_id": "AUTH-025", "section": "A"},
                result="ok",
                at=now,
            )
        )

    with transactional(factory) as session:
        row = session.execute(
            select(AuditLogModel).where(AuditLogModel.action == "task.create")
        ).scalar_one()
        assert row.payload_json == {"task_id": "AUTH-025", "section": "A"}
        assert row.surface == "mcp"
        assert row.result == "ok"


def test_cascade_delete_project_drops_revisions_and_audit(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        proj_id = _add_project(session, "casc")
        session.add(
            RevisionModel(
                revision_id=str(ULID()),
                project_id=proj_id,
                entity_kind="task",
                entity_id=1,
                author="a",
                at=now,
                diff="d",
            )
        )
        session.add(
            AuditLogModel(
                project_id=proj_id,
                actor="a",
                surface="cli",
                action="x.y",
                payload_json={},
                result="ok",
                at=now,
            )
        )

    with transactional(factory) as session:
        session.delete(session.get(ProjectModel, proj_id))

    with engine_with_schema.connect() as conn:
        rev = conn.execute(
            text("SELECT COUNT(*) FROM revision WHERE project_id = :p"), {"p": proj_id}
        ).scalar_one()
        au = conn.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE project_id = :p"), {"p": proj_id}
        ).scalar_one()
    assert (rev, au) == (0, 0)
