"""FLW-003 (RFC 33 F1, §3.1): длительности задачи — из журнала переходов.

До правки ``_derive_state_durations`` искал в диффах ревизий ключи
``new_status``/``to``, а ``update_status`` пишет ``{"op": "status", "old",
"new"}``: ``in_progress_hours`` и ``blocked_hours`` были NULL у всех строк
``task_metrics``. Переходы теперь читаются из ``activity_event``:
``task.checked_out`` → in_progress, ``task.status_changed`` (с ``old_status``)
→ ``new_status``, ``task.completed`` → done. Событие без ``old_status`` —
дубль MCP до FLW-001 — пропускается.

Таймлайн собирается из событий с явными метками времени — ожидание считается
вручную, а не вызовом кода под тестом.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, TaskMetricsModel, TaskModel
from cod_doc.services import metrics_service
from tests.services.test_checkout_service import _seed_task

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
AUTHOR = "agent:dakh/claude-code"


@pytest.fixture
def sf(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _event(s: Session, pid: int, kind: str, at: datetime, **payload: Any) -> None:
    s.add(
        ActivityEventModel(
            id=str(uuid.uuid4()),
            project_id=pid,
            ts=at,
            actor_kind="agent",
            actor_id=AUTHOR,
            kind=kind,
            scope_kind="task",
            scope_id="CO-001",
            payload=payload,
            summary=kind,
        )
    )


def _lifecycle(s: Session, pid: int, *, duplicates: bool = False) -> None:
    """todo → checkout (+1h) → blocked (+3h) → in_progress (+5h) → done (+6h)."""
    _event(
        s,
        pid,
        "task.checked_out",
        T0 + timedelta(hours=1),
        old_status="todo",
        new_status="in_progress",
    )
    _event(
        s,
        pid,
        "task.status_changed",
        T0 + timedelta(hours=3),
        old_status="in_progress",
        new_status="blocked",
    )
    if duplicates:
        # Дубли MCP до FLW-001: тот же переход и мусорный, оба без old_status.
        _event(
            s, pid, "task.status_changed", T0 + timedelta(hours=3, minutes=1), new_status="blocked"
        )
        _event(s, pid, "task.status_changed", T0 + timedelta(hours=4), new_status="in-progress")
    _event(
        s,
        pid,
        "task.status_changed",
        T0 + timedelta(hours=5),
        old_status="blocked",
        new_status="in_progress",
    )
    _event(s, pid, "task.completed", T0 + timedelta(hours=6), commit_sha="abc1234")
    task = s.scalars(select(TaskModel).where(TaskModel.task_id == "CO-001")).one()
    task.status = "done"
    task.created = T0
    task.completed_at = T0 + timedelta(hours=6)


def _derive(s: Session) -> tuple[float | None, float | None]:
    task = s.scalars(select(TaskModel).where(TaskModel.task_id == "CO-001")).one()
    return metrics_service._derive_state_durations(s, task)


def test_durations_come_from_transition_events(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        pid = _seed_task(s, status="todo")
        _lifecycle(s, pid)
        s.flush()
        assert _derive(s) == (3.0, 2.0)  # in_progress: 1→3 и 5→6; blocked: 3→5


def test_duplicates_without_old_status_are_ignored(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        pid = _seed_task(s, status="todo")
        _lifecycle(s, pid, duplicates=True)
        s.flush()
        assert _derive(s) == (3.0, 2.0)


def test_no_events_gives_no_durations(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        _seed_task(s, status="todo")
        assert _derive(s) == (None, None)


def test_rebuild_fills_rows_idempotently_and_reports(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        pid = _seed_task(s, status="todo")
        _lifecycle(s, pid)
    with transactional(sf) as s:
        first = metrics_service.rebuild(s, project_id=pid, author=AUTHOR)
    with transactional(sf) as s:
        second = metrics_service.rebuild(s, project_id=pid, author=AUTHOR)
    with transactional(sf) as s:
        row = s.scalars(select(TaskMetricsModel)).one()
        events = s.scalars(
            select(ActivityEventModel.payload).where(ActivityEventModel.kind == "metrics.rebuilt")
        ).all()

    assert (row.in_progress_hours, row.blocked_hours, row.duration_hours) == (3.0, 2.0, 6.0)
    assert (first.tasks, first.created, first.updated, first.with_in_progress) == (1, 1, 0, 1)
    assert (second.tasks, second.created, second.updated, second.with_in_progress) == (1, 0, 0, 1)
    assert len(events) == 2
    assert events[0]["created"] == 1


def test_mcp_flow_metrics_rebuild(
    monkeypatch: pytest.MonkeyPatch, sf: sessionmaker[Session]
) -> None:
    from mcp.server.fastmcp import FastMCP

    from cod_doc.mcp.tools import flow_tools

    with transactional(sf) as s:
        pid = _seed_task(s, status="todo")
        _lifecycle(s, pid)
    monkeypatch.setattr(flow_tools, "session_factory", lambda project: (sf, None))
    monkeypatch.setattr(flow_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    flow_tools.register(mcp)
    tool = mcp._tool_manager._tools["flow_metrics_rebuild"].fn

    dry = tool(project="co", author=AUTHOR, dry_run=True)
    with transactional(sf) as s:
        assert s.scalars(select(TaskMetricsModel)).all() == []
    real = tool(project="co", author=AUTHOR)
    with transactional(sf) as s:
        row = s.scalars(select(TaskMetricsModel)).one()

    assert dry == {
        "tasks": 1,
        "created": 1,
        "updated": 0,
        "with_in_progress": 1,
        "without_events": 0,
        "dry_run": True,
    }
    assert real["created"] == 1
    assert real["dry_run"] is False
    assert (row.in_progress_hours, row.blocked_hours) == (3.0, 2.0)


def test_rebuild_is_idempotent_for_done_task_without_completed_at(
    sf: sessionmaker[Session],
) -> None:
    """Легаси-строка done без completed_at: длительность не растёт от прогона к прогону.

    На живой БД cod-doc таких 34; с отсчётом от now() второй прогон менял их все.
    """
    with transactional(sf) as s:
        pid = _seed_task(s, status="todo")
        task = s.scalars(select(TaskModel).where(TaskModel.task_id == "CO-001")).one()
        task.status = "done"
        task.created = T0
        task.completed_at = None
        task.last_updated = T0 + timedelta(hours=5)
    with transactional(sf) as s:
        metrics_service.rebuild(s, project_id=pid, author=AUTHOR)
    with transactional(sf) as s:
        again = metrics_service.rebuild(s, project_id=pid, author=AUTHOR)
    with transactional(sf) as s:
        row = s.scalars(select(TaskMetricsModel)).one()

    assert again.updated == 0
    assert row.duration_hours == 5.0


def test_rebuild_dry_run_writes_nothing(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        pid = _seed_task(s, status="todo")
        _lifecycle(s, pid)
    with transactional(sf) as s:
        report = metrics_service.rebuild(s, project_id=pid, author=AUTHOR, dry_run=True)
        assert s.scalars(select(TaskMetricsModel)).all() == []
        assert (
            s.scalars(
                select(ActivityEventModel).where(ActivityEventModel.kind == "metrics.rebuilt")
            ).all()
            == []
        )
    assert (report.tasks, report.created, report.with_in_progress) == (1, 1, 1)
