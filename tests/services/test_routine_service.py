"""PCA-211: RoutineService — cron health checks + run_now."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    ApprovalModel,
    ProjectModel,
    RoutineModel,
)
from cod_doc.services import approval_service as approvals
from cod_doc.services import doc_service, projection_service
from cod_doc.services import routine_service as routines
from cod_doc.services.routine_service import RoutineNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session, slug: str = "rt", root_path: str | None = None) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(
        slug=slug,
        title=slug,
        root_path=root_path or f"/tmp/{slug}",
        config_json={},
    )
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_create_persists_routine(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        r = routines.create(
            session,
            proj_id,
            name="approval_stale_check",
            check_name="approval_stale",
            trigger="cron",
            cron="0 * * * *",
            on_finding="comment_only",
        )
        assert r.name == "approval_stale_check"
        assert r.cron == "0 * * * *"
        assert r.enabled is True


def test_create_validates_check_name(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(ValueError, match="unknown check_name"):
            routines.create(session, proj_id, name="x", check_name="not_a_check", trigger="manual")


def test_create_cron_requires_cron_expr(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(ValueError, match="trigger='cron' requires"):
            routines.create(session, proj_id, name="x", check_name="approval_stale", trigger="cron")


def test_create_validates_on_finding(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(ValueError, match="on_finding"):
            routines.create(
                session,
                proj_id,
                name="x",
                check_name="approval_stale",
                trigger="manual",
                on_finding="bogus",
            )


def test_list_filters_enabled_only(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        routines.create(session, proj_id, name="r1", check_name="approval_stale", trigger="manual")
        routines.create(session, proj_id, name="r2", check_name="approval_stale", trigger="manual")
        routines.update_status(session, proj_id, "r2", enabled=False)

        enabled = routines.list_routines(session, proj_id, enabled_only=True)
        assert {r.name for r in enabled} == {"r1"}
        all_routines = routines.list_routines(session, proj_id)
        assert {r.name for r in all_routines} == {"r1", "r2"}


def test_update_status_unknown_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(RoutineNotFoundError):
            routines.update_status(session, proj_id, "no-such", enabled=False)


def test_delete_cascades_to_runs(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        routines.create(session, proj_id, name="r1", check_name="approval_stale", trigger="manual")
        routines.run_now(session, proj_id, "r1")
        routines.delete(session, proj_id, "r1")
        assert routines.get(session, proj_id, "r1") is None


def test_run_now_emits_routine_fired_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        routines.create(session, proj_id, name="r1", check_name="approval_stale", trigger="manual")
        run = routines.run_now(session, proj_id, "r1")
        assert run.status == "done"

    with transactional(factory) as session:
        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.kind == "routine.fired",
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].scope_id == "r1"


def test_run_now_skip_concurrency_returns_existing_running(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        routines.create(
            session,
            proj_id,
            name="r1",
            check_name="approval_stale",
            trigger="manual",
            concurrency="skip",
        )
        # Manually plant a 'running' row to simulate concurrent execution.
        from cod_doc.infra.models import RoutineRunModel

        routine_row = session.execute(
            select(RoutineModel).where(RoutineModel.name == "r1")
        ).scalar_one()
        session.add(
            RoutineRunModel(
                routine_id=routine_row.row_id,
                status="running",
            )
        )
        session.flush()

        result = routines.run_now(session, proj_id, "r1")
        # Returns the existing 'running' row instead of starting a new one.
        assert result.status == "running"


def test_approval_stale_check_expires_pending_approvals(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        # Create a pending approval that is already past its expires_at.
        a = approvals.request(
            session, proj_id, approval_type="manual", requested_by="x", expires_in_hours=1
        )
        m = session.execute(
            select(ApprovalModel).where(ApprovalModel.approval_id == a.approval_id)
        ).scalar_one()
        m.expires_at = datetime.now(UTC) - timedelta(hours=2)
        session.flush()

        routines.create(
            session, proj_id, name="approval_stale", check_name="approval_stale", trigger="manual"
        )
        run = routines.run_now(session, proj_id, "approval_stale")
        assert run.findings_count == 1

    with transactional(factory) as session:
        a_after = approvals.get(session, proj_id, a.approval_id)
        assert a_after.status == "expired"
        assert a_after.decision_comment == "expired by routine"


def test_doc_drift_routine_payload_contains_project_summary(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session, root_path=str(tmp_path))
        synced = doc_service.create(
            session,
            project_id=proj_id,
            doc_key="synced",
            type=DocumentType.GUIDE,
            status=DocumentStatus.ACTIVE,
            title="Synced",
            owner="docs",
            author="human:test",
        )
        doc_service.create(
            session,
            project_id=proj_id,
            doc_key="missing",
            type=DocumentType.GUIDE,
            status=DocumentStatus.ACTIVE,
            title="Missing",
            owner="docs",
            author="human:test",
        )
        projection_service.export_document(session, synced.row_id, root_path=tmp_path)
        routines.create(
            session, proj_id, name="doc_drift", check_name="doc_drift", trigger="manual"
        )

        run = routines.run_now(session, proj_id, "doc_drift")

        assert run.findings_count == 1

    with transactional(factory) as session:
        event = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "routine.found_issue")
        ).scalar_one()
        result = event.payload["result"]
        assert result["total_docs"] == 2
        assert result["counts"]["in_sync"] == 1
        assert result["counts"]["missing"] == 1
        assert result["findings"][0]["doc_key"] == "missing"


def test_history_returns_recent_runs_newest_first(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        routines.create(session, proj_id, name="r1", check_name="approval_stale", trigger="manual")
        for _ in range(3):
            routines.run_now(session, proj_id, "r1")
        history = routines.history(session, proj_id, "r1", limit=10)
        assert len(history) == 3
        # Newest first.
        assert history[0].started_at >= history[-1].started_at


def test_tick_handles_sqlite_naive_last_run(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """SQLite may round-trip timezone-aware datetimes as naive values."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        routines.create(
            session,
            proj_id,
            name="r1",
            check_name="approval_stale",
            trigger="cron",
            cron="*/15 * * * *",
        )
        routine_row = session.execute(
            select(RoutineModel).where(RoutineModel.name == "r1")
        ).scalar_one()
        from cod_doc.infra.models import RoutineRunModel

        session.add(
            RoutineRunModel(
                routine_id=routine_row.row_id,
                status="done",
                started_at=datetime.now(UTC).replace(tzinfo=None),
                findings_count=0,
            )
        )
        session.flush()

        assert routines.tick(session, proj_id) == []
