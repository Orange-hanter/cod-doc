"""PCA-111: ActivityEmitter + activity query helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import activity_service as activity
from cod_doc.services.run_context import set_current_run_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session, slug: str = "ap") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_emit_creates_event_with_defaults(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        ev = activity.emit(
            session,
            proj_id,
            "task.created",
            scope_kind="task",
            scope_id="T-001",
            summary="Task T-001 created",
        )
        assert ev.id is not None
        assert ev.kind == "task.created"
        assert ev.actor_kind == "system"
        assert ev.run_id is None
        assert ev.payload == {}


def test_emit_picks_up_run_id_from_contextvar(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        try:
            set_current_run_id("01J0ACTIVITY")
            ev = activity.emit(session, proj_id, "task.status_changed")
            assert ev.run_id == "01J0ACTIVITY"
        finally:
            set_current_run_id(None)


def test_emit_explicit_run_id_overrides_contextvar(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        try:
            set_current_run_id("from-context")
            ev = activity.emit(session, proj_id, "k", run_id="explicit")
            assert ev.run_id == "explicit"
        finally:
            set_current_run_id(None)


def test_list_events_returns_newest_first(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        for i in range(3):
            activity.emit(session, proj_id, f"e.{i}", ts=datetime(2026, 1, 1 + i, tzinfo=UTC))

    with transactional(factory) as session:
        result = activity.list_events(session, proj_id)
        assert result["total"] == 3
        kinds = [e["kind"] for e in result["items"]]
        assert kinds == ["e.2", "e.1", "e.0"]


def test_list_events_filters_by_scope(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        activity.emit(session, proj_id, "task.created", scope_kind="task", scope_id="T-001")
        activity.emit(session, proj_id, "doc.updated", scope_kind="doc", scope_id="MASTER")
        activity.emit(session, proj_id, "task.completed", scope_kind="task", scope_id="T-001")

    with transactional(factory) as session:
        only_task = activity.list_events(session, proj_id, scope_kind="task")
        assert only_task["total"] == 2
        only_t001 = activity.list_events(session, proj_id, scope_kind="task", scope_id="T-001")
        assert only_t001["total"] == 2


def test_list_events_filters_by_kind_and_actor(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        activity.emit(session, proj_id, "approval.requested", actor_kind="orchestrator")
        activity.emit(session, proj_id, "approval.resolved", actor_kind="human")
        activity.emit(session, proj_id, "doc.updated", actor_kind="orchestrator")

    with transactional(factory) as session:
        by_kind = activity.list_events(session, proj_id, kind="approval.requested")
        assert by_kind["total"] == 1
        by_actor = activity.list_events(session, proj_id, actor_kind="orchestrator")
        assert by_actor["total"] == 2


def test_list_events_filters_by_time_range(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    base = datetime(2026, 5, 1, tzinfo=UTC)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        for i in range(5):
            activity.emit(session, proj_id, "k", ts=base + timedelta(days=i))

    with transactional(factory) as session:
        result = activity.list_events(
            session,
            proj_id,
            since=base + timedelta(days=1),
            until=base + timedelta(days=3),
        )
        assert result["total"] == 3


def test_list_events_pagination(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        for i in range(10):
            activity.emit(session, proj_id, f"e.{i}", ts=datetime(2026, 1, 1, i, 0, tzinfo=UTC))

    with transactional(factory) as session:
        page = activity.list_events(session, proj_id, limit=3, offset=2)
        assert page["total"] == 10
        assert page["limit"] == 3
        assert page["offset"] == 2
        assert len(page["items"]) == 3


def test_events_for_run_returns_oldest_first(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        activity.emit(
            session, proj_id, "step.1", run_id="R1", ts=datetime(2026, 1, 1, 10, tzinfo=UTC)
        )
        activity.emit(
            session, proj_id, "step.2", run_id="R1", ts=datetime(2026, 1, 1, 11, tzinfo=UTC)
        )
        activity.emit(session, proj_id, "other", run_id="R2")

    with transactional(factory) as session:
        events = activity.events_for_run(session, proj_id, "R1")
        assert [e["kind"] for e in events] == ["step.1", "step.2"]


def test_events_for_run_excludes_other_projects(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p1 = _seed_project(session, "p1")
        p2 = _seed_project(session, "p2")
        activity.emit(session, p1, "k1", run_id="R")
        activity.emit(session, p2, "k2", run_id="R")

    with transactional(factory) as session:
        ev1 = activity.events_for_run(session, p1, "R")
        assert [e["kind"] for e in ev1] == ["k1"]


def test_emit_persists_payload_and_summary(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        activity.emit(
            session,
            proj_id,
            "task.status_changed",
            payload={"from": "pending", "to": "in-progress"},
            summary="T-001 → in-progress",
        )

    with transactional(factory) as session:
        ev = session.execute(select(ActivityEventModel)).scalar_one()
        assert ev.payload == {"from": "pending", "to": "in-progress"}
        assert ev.summary == "T-001 → in-progress"


def test_emit_event_id_is_unique(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        ids = {activity.emit(session, proj_id, "k").id for _ in range(20)}
        assert len(ids) == 20
