"""STB-010: exercise best-effort / degraded paths previously ``pragma: no cover``.

Each test drives a defensive failure branch so a regression that changes its
behaviour is caught by CI instead of slipping through silently. Covers:

- ``event_bus._flush_pending_events`` / ``_drop_pending_events`` (after commit/rollback)
- ``run_context._open_session_for_project`` swallow-on-error
- ``run_context.start_orchestrator_run`` / ``finalize_orchestrator_run`` DB-failure paths
- ``routine_service.run_now`` defensive guard (check raises → run marked failed)
- ``adapters.registry._load_plugins`` bad-plugin-file warning
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory
from cod_doc.infra.models import ProjectModel, RoutineRunModel
from cod_doc.services import event_bus, routine_service, run_context

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def _seed_project(session: Session, slug: str = "deg") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


# ── event_bus: deferred-event flush / drop ───────────────────────────────────


def test_flush_pending_events_publishes_on_commit(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    published: list[tuple[str, str, dict | None]] = []
    monkeypatch.setattr(
        event_bus,
        "publish_sync",
        lambda project, kind, payload=None: published.append((project, kind, payload)),
    )
    factory = make_session_factory(engine_with_schema)
    session = factory()
    try:
        _seed_project(session, "eb-flush")  # real INSERT → commit fires after_commit
        event_bus.queue_emit(session, "demo", "task.created", task_id="T-1")
        session.commit()
    finally:
        session.close()
    assert ("demo", "task.created", {"task_id": "T-1"}) in published


def test_flush_pending_events_noop_without_pending(monkeypatch) -> None:
    import types

    called: list = []
    monkeypatch.setattr(event_bus, "publish_sync", lambda *a, **k: called.append(a))
    fake = types.SimpleNamespace(info={})
    event_bus._flush_pending_events(fake)  # type: ignore[arg-type]  # no pending → early return
    assert called == []


def test_drop_pending_events_discards_on_rollback(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(event_bus, "publish_sync", lambda *a, **k: pytest.fail("must not publish"))
    factory = make_session_factory(engine_with_schema)
    session = factory()
    try:
        _seed_project(session, "eb-drop")
        event_bus.queue_emit(session, "demo", "task.created", task_id="T-2")
        session.rollback()  # fires after_rollback → _drop_pending_events
        assert event_bus._PENDING_KEY not in session.info
    finally:
        session.close()


# ── run_context: best-effort DB session + orchestrator run lifecycle ──────────


def test_open_session_for_project_swallows_errors(monkeypatch) -> None:
    import cod_doc.infra.db as db

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "make_engine", boom)
    assert run_context._open_session_for_project("/no/such/project") == (None, None)


def test_orchestrator_run_lifecycle_survives_db_failure(monkeypatch) -> None:
    """start/finalize hit their best-effort except, yet the contextvar still
    flows through and resets cleanly."""

    def boom_factory():
        raise RuntimeError("session boom")

    # Pretend the project has a DB so the (failing) write branch is taken.
    monkeypatch.setattr(run_context, "_open_session_for_project", lambda p: (boom_factory, 1))

    assert run_context.get_current_run_id() is None
    token = run_context.start_orchestrator_run(project_path="/x", run_id="RUN-1")
    assert run_context.get_current_run_id() == "RUN-1"  # set despite DB write failure
    run_context.finalize_orchestrator_run(
        token, project_path="/x", run_id="RUN-1", status="done"
    )
    assert run_context.get_current_run_id() is None  # reset after finalize


# ── routine_service: defensive guard when a check raises ──────────────────────


def test_run_now_marks_failed_and_reraises_on_check_error(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    check_name = next(iter(routine_service.CHECK_CATALOG))

    def boom(session, project_id, **kw):
        raise RuntimeError("check exploded")

    monkeypatch.setitem(routine_service.CHECK_CATALOG, check_name, boom)

    factory = make_session_factory(engine_with_schema)
    session = factory()
    try:
        project_id = _seed_project(session, "rt-fail")
        routine_service.create(
            session,
            project_id,
            name="boomer",
            check_name=check_name,
            trigger="cron",
            cron="0 * * * *",
        )
        session.flush()
        with pytest.raises(RuntimeError, match="check exploded"):
            routine_service.run_now(session, project_id, "boomer")
        # The defensive guard persisted a failed run row before re-raising.
        run = session.execute(select(RoutineRunModel)).scalars().first()
        assert run is not None
        assert run.status == "failed"
        assert "check exploded" in (run.error or "")
    finally:
        session.close()


# ── adapters.registry: malformed plugin file warns, never raises ──────────────


def test_load_plugins_warns_on_bad_adapter_file(tmp_path: Path, monkeypatch) -> None:
    from pathlib import Path as _Path

    from cod_doc.agent.adapters import registry

    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    cod_dir = tmp_path / ".cod-doc"
    cod_dir.mkdir()
    (cod_dir / "adapters.json").write_text("{ not valid json ]", encoding="utf-8")
    # Reset the one-shot gate so _load_plugins actually runs the body.
    monkeypatch.setattr(registry, "_plugins_loaded", False)

    with pytest.warns(UserWarning, match="Failed to load adapter plugins"):
        registry._load_plugins()
