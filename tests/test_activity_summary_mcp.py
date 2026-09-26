"""AFT-008 (RFC 27 F9): MCP ``activity_summary`` + ``activity_list(actor_id=...)``.

``activity_summary`` — один вызов вместо прямого SQL «события за N дней по
дням и actor_kind»: since/until разбираются через ``task_service.parse_since``,
group_by None → ['day'], недопустимый ключ — ValueError со списком
допустимых. ``activity_list`` пробрасывает ``actor_id`` в сервис. События
засеиваются через ``activity_service.emit`` с литеральными ts/actor_kind/
actor_id. Эталоны — литералы; строить ожидание вызовом
``activity_service.summarize``/``list_events`` запрещено спекой. Исключение —
тест сходимости: сумма n против total тула ``activity_list``, сравнение двух
тулов и есть его предмет.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import activity_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


def _dt(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _seed(session: Session) -> int:
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="pr", title="pr", root_path="/tmp/pr", config={})
    )
    proj.created = _dt(9, 1, 10)
    proj.updated = _dt(9, 1, 10)
    session.flush()
    pid = proj.row_id
    # Старше since='2026-09-12' — не должен попадать в summary.
    activity_service.emit(
        session,
        pid,
        "doc.updated",
        actor_kind="human",
        actor_id="human:dakh",
        scope_kind="doc",
        scope_id="doc:a",
        ts=_dt(9, 1, 10),
    )
    activity_service.emit(
        session,
        pid,
        "doc.updated",
        actor_kind="human",
        actor_id="human:dakh",
        scope_kind="doc",
        scope_id="doc:a",
        ts=_dt(9, 12, 10),
    )
    activity_service.emit(
        session,
        pid,
        "task.status_changed",
        actor_kind="agent",
        actor_id="agent:claude-opus-5",
        scope_kind="task",
        scope_id="AFT-008-001",
        ts=_dt(9, 12, 15),
    )
    activity_service.emit(
        session,
        pid,
        "doc.updated",
        actor_kind="human",
        actor_id="human:dakh",
        scope_kind="doc",
        scope_id="doc:b",
        ts=_dt(9, 20, 9),
    )
    activity_service.emit(
        session,
        pid,
        "task.created",
        actor_kind="agent",
        actor_id="agent:claude-opus-5",
        scope_kind="task",
        scope_id="AFT-008-002",
        ts=_dt(9, 25, 8),
    )
    activity_service.emit(
        session,
        pid,
        "routine.fired",
        actor_kind="routine",
        actor_id="routine:doc_drift_daily",
        scope_kind="project",
        scope_id="pr",
        ts=_dt(9, 25, 9, 30),
    )
    session.flush()
    return pid


def _tools(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> dict[str, Any]:
    from cod_doc.mcp.tools import activity_tools

    factory = make_session_factory(engine)
    with transactional(factory) as s:
        proj_id = _seed(s)
    monkeypatch.setattr(activity_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(activity_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    activity_tools.register(mcp)
    return {
        "activity_summary": mcp._tool_manager._tools["activity_summary"].fn,
        "activity_list": mcp._tool_manager._tools["activity_list"].fn,
    }


def test_summary_14_days_by_day_and_actor_kind_one_call(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    activity_summary = _tools(monkeypatch, engine)["activity_summary"]

    result = activity_summary(
        project="pr",
        since="2026-09-12",
        group_by=["day", "actor_kind"],
    )

    assert result == [
        {"day": "2026-09-12", "actor_kind": "agent", "n": 1},
        {"day": "2026-09-12", "actor_kind": "human", "n": 1},
        {"day": "2026-09-20", "actor_kind": "human", "n": 1},
        {"day": "2026-09-25", "actor_kind": "agent", "n": 1},
        {"day": "2026-09-25", "actor_kind": "routine", "n": 1},
    ]


def test_summary_sum_matches_activity_list_total(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = _tools(monkeypatch, engine)

    summary = tools["activity_summary"](
        project="pr",
        since="2026-09-12",
        until="2026-09-25T09:30:00+00:00",
        group_by=["kind"],
    )
    listed = tools["activity_list"](
        project="pr",
        since="2026-09-12",
        until="2026-09-25T09:30:00+00:00",
    )

    assert sum(row["n"] for row in summary) == listed["total"] == 5


def test_summary_invalid_group_by_is_error(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    activity_summary = _tools(monkeypatch, engine)["activity_summary"]

    with pytest.raises(ValueError) as excinfo:
        activity_summary(project="pr", since="2026-09-12", group_by=["week"])

    message = str(excinfo.value)
    for allowed in ("day", "actor_kind", "kind", "scope_kind"):
        assert allowed in message


def test_summary_default_group_by_day(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    activity_summary = _tools(monkeypatch, engine)["activity_summary"]

    result = activity_summary(project="pr", since="2026-09-12")

    assert result == [
        {"day": "2026-09-12", "n": 2},
        {"day": "2026-09-20", "n": 1},
        {"day": "2026-09-25", "n": 2},
    ]
    for row in result:
        assert set(row) == {"day", "n"}


def test_activity_list_actor_id(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    activity_list = _tools(monkeypatch, engine)["activity_list"]

    result = activity_list(project="pr", actor_id="human:dakh", limit=50)

    assert {item["kind"] for item in result["items"]} == {"doc.updated"}
    assert result["total"] == 3
