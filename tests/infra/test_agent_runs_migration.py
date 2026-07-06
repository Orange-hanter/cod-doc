"""PCA-030: agent_run table + run_id columns on revision/audit_log."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, select

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    AgentRunModel,
    AuditLogModel,
    ProjectModel,
    RevisionModel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    from tests._alembic import run_alembic_upgrade

    run_alembic_upgrade(db_url)


def _run_alembic_downgrade(db_url: str, target: str) -> None:
    from tests._alembic import run_alembic_downgrade

    run_alembic_downgrade(db_url, target)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'agent_runs.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _add_project(session) -> int:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


# --------------------------------------------------------------------------- #
# Schema invariants                                                            #
# --------------------------------------------------------------------------- #


def test_agent_run_table_exists(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    insp = inspect(engine_with_schema)
    assert "agent_run" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("agent_run")}
    expected = {
        "row_id",
        "run_id",
        "project_id",
        "started_at",
        "finished_at",
        "wake_reason",
        "triggering_task_id",
        "triggering_doc_ref",
        "llm_calls",
        "llm_tokens_in",
        "llm_tokens_out",
        "status",
        "summary",
    }
    assert expected <= cols, f"missing columns: {expected - cols}"


def test_run_id_column_added_to_revision_and_audit_log(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    insp = inspect(engine_with_schema)
    rev_cols = {c["name"] for c in insp.get_columns("revision")}
    audit_cols = {c["name"] for c in insp.get_columns("audit_log")}
    assert "run_id" in rev_cols
    assert "run_id" in audit_cols


def test_run_id_unique_constraint_on_agent_run(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Two rows with same run_id violate UNIQUE; second tx aborts."""
    from sqlalchemy.exc import IntegrityError

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        session.add(AgentRunModel(run_id="run-1", project_id=proj_id))

    with pytest.raises(IntegrityError), transactional(factory) as session:
        session.add(AgentRunModel(run_id="run-1", project_id=proj_id))


# --------------------------------------------------------------------------- #
# CRUD                                                                          #
# --------------------------------------------------------------------------- #


def test_agent_run_round_trip(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        run = AgentRunModel(
            run_id="01J0AGENT0001",
            project_id=proj_id,
            wake_reason="task_assigned",
            triggering_task_id="PCA-030",
            llm_calls=3,
            llm_tokens_in=120,
            llm_tokens_out=80,
            status="running",
        )
        session.add(run)
        session.flush()
        assert run.row_id is not None
        assert run.started_at is not None  # server_default

    with transactional(factory) as session:
        loaded = session.execute(
            select(AgentRunModel).where(AgentRunModel.run_id == "01J0AGENT0001")
        ).scalar_one()
        assert loaded.wake_reason == "task_assigned"
        assert loaded.llm_calls == 3
        assert loaded.status == "running"


def test_revision_run_id_linkage(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        run = AgentRunModel(run_id="01J0LINK", project_id=proj_id)
        session.add(run)
        session.flush()

        rev = RevisionModel(
            revision_id="01J0REV001",
            project_id=proj_id,
            entity_kind="task",
            entity_id=1,
            author="agent",
            diff='{"op":"create"}',
            run_id="01J0LINK",
        )
        session.add(rev)
        session.flush()

    with transactional(factory) as session:
        rev_loaded = session.execute(
            select(RevisionModel).where(RevisionModel.run_id == "01J0LINK")
        ).scalar_one()
        assert rev_loaded.revision_id == "01J0REV001"


def test_audit_log_run_id_linkage(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        entry = AuditLogModel(
            project_id=proj_id,
            actor="agent",
            surface="mcp",
            action="task.create",
            payload_json={"task_id": "PCA-030"},
            result="ok",
            run_id="01J0AUDIT",
        )
        session.add(entry)
        session.flush()

    with transactional(factory) as session:
        loaded = session.execute(
            select(AuditLogModel).where(AuditLogModel.run_id == "01J0AUDIT")
        ).scalar_one()
        assert loaded.action == "task.create"


def test_revision_without_run_id_remains_supported(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Human / external mutations don't have a run_id — column is nullable."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        rev = RevisionModel(
            revision_id="01J0HUMAN",
            project_id=proj_id,
            entity_kind="document",
            entity_id=1,
            author="human:cli",
            diff='{"op":"manual"}',
        )
        session.add(rev)
        session.flush()
        assert rev.run_id is None


# --------------------------------------------------------------------------- #
# Migration up/down round-trip                                                 #
# --------------------------------------------------------------------------- #


def test_migration_round_trip(db_url: str) -> None:
    """upgrade head → downgrade -1 → upgrade head must succeed."""
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    insp = inspect(engine)
    assert "agent_run" in insp.get_table_names()
    engine.dispose()

    _run_alembic_downgrade(db_url, "0009_task_normalized_title")
    engine = make_engine(db_url)
    insp = inspect(engine)
    assert "agent_run" not in insp.get_table_names()
    rev_cols = {c["name"] for c in insp.get_columns("revision")}
    assert "run_id" not in rev_cols
    engine.dispose()

    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    insp = inspect(engine)
    assert "agent_run" in insp.get_table_names()
    rev_cols = {c["name"] for c in insp.get_columns("revision")}
    assert "run_id" in rev_cols
    engine.dispose()
