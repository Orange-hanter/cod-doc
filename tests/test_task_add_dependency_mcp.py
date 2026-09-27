"""ADO-202: MCP-тул ``task_add_dependency`` поверх ``task_service.add_dependency``.

Тул регистрируется на свежем FastMCP, ``session_factory`` и
``require_project_id`` подменяются (образец —
``tests/services/test_task_hub_scope.py::test_mcp_task_remove_dependency_hub``).
Эталоны — литералы и прямые SELECT по таблице ``dependency``; ожидание не
строится вызовом ``add_dependency`` / ``dependency_warnings``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select, update

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services import task_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def engine_with_schema(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed_project(session: Session, slug: str) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope=f"{slug}-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make(
    session: Session,
    ids: tuple[int, int, int],
    tid: str,
    *,
    blocked_by: list[str] | None = None,
) -> None:
    p, pl, s = ids
    task_service.create(
        session,
        project_id=p,
        plan_id=pl,
        section_id=s,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
        blocked_by=blocked_by,
    )


def _edges(factory: sessionmaker[Session], project_id: int) -> list[tuple[str, str, str | None]]:
    """Все рёбра проекта как (from task_id, to task_id, note) прямым SELECT."""
    src = TaskModel.__table__.alias("src")
    dst = TaskModel.__table__.alias("dst")
    with transactional(factory) as session:
        rows = session.execute(
            select(src.c.task_id, dst.c.task_id, DependencyModel.note)
            .join(src, src.c.row_id == DependencyModel.from_task_id)
            .join(dst, dst.c.row_id == DependencyModel.to_task_id)
            .where(src.c.project_id == project_id)
            .order_by(src.c.task_id, dst.c.task_id)
        ).all()
    return [(a, b, n) for a, b, n in rows]


def _tool(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    pids: dict[str, int],
) -> Any:
    from cod_doc.mcp.tools import task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: pids[project])
    mcp = FastMCP("test")
    task_tools.register(mcp)
    return mcp._tool_manager._tools["task_add_dependency"].fn


def _seed(
    engine: Engine, tasks: list[tuple[str, list[str] | None]]
) -> tuple[sessionmaker[Session], int]:
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        ids = _seed_project(session, "alpha")
        for tid, blocked_by in tasks:
            _make(session, ids, tid, blocked_by=blocked_by)
    return factory, ids[0]


def test_mcp_add_dependency_creates_edge(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema, [("TA-001", None), ("TA-002", None)])
    tool = _tool(monkeypatch, factory, {"alpha": pid})

    out = tool(project="alpha", task_id="TA-002", blocker_id="TA-001", note="нужен TA-001")

    assert out["op"] == "add_dependency"
    assert out["warnings"] == []
    assert out["task_id"] == "TA-002"
    assert _edges(factory, pid) == [("TA-002", "TA-001", "нужен TA-001")]


def test_mcp_repeat_is_noop_and_adopt(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema, [("TA-001", None), ("TA-002", None)])
    tool = _tool(monkeypatch, factory, {"alpha": pid})

    tool(project="alpha", task_id="TA-002", blocker_id="TA-001", note="первая")
    again = tool(project="alpha", task_id="TA-002", blocker_id="TA-001", note="первая")
    assert again["op"] is None

    adopted = tool(
        project="alpha", task_id="TA-002", blocker_id="TA-001", note="первая", adopt=True
    )
    assert adopted["op"] == "adopt_dependency"
    assert _edges(factory, pid) == [("TA-002", "TA-001", "первая")]

    changed = tool(project="alpha", task_id="TA-002", blocker_id="TA-001", note="вторая")
    assert changed["op"] == "update_dependency_note"
    assert _edges(factory, pid) == [("TA-002", "TA-001", "вторая")]


def test_mcp_cycle_error_names_path(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    # TA-003 → TA-002 → TA-001; ребро TA-001 → TA-003 замкнуло бы цикл.
    factory, pid = _seed(
        engine_with_schema,
        [("TA-001", None), ("TA-002", ["TA-001"]), ("TA-003", ["TA-002"])],
    )
    tool = _tool(monkeypatch, factory, {"alpha": pid})

    with pytest.raises(ValueError, match="cycle") as exc_info:
        tool(project="alpha", task_id="TA-001", blocker_id="TA-003", note="цикл")

    message = str(exc_info.value)
    for tid in ("TA-001", "TA-002", "TA-003"):
        assert tid in message
    assert _edges(factory, pid) == [
        ("TA-002", "TA-001", None),
        ("TA-003", "TA-002", None),
    ]


def test_mcp_warnings_blocker_closed(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema, [("TA-001", None), ("TA-002", None)])
    with transactional(factory) as session:
        session.execute(
            update(TaskModel)
            .where(TaskModel.project_id == pid, TaskModel.task_id == "TA-001")
            .values(status="done")
        )
    tool = _tool(monkeypatch, factory, {"alpha": pid})

    out = tool(project="alpha", task_id="TA-002", blocker_id="TA-001", note="после TA-001")

    assert "blocker_closed" in [w["code"] for w in out["warnings"]]


def test_mcp_unknown_task_is_error(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        beta = _seed_project(session, "beta")
        _make(session, alpha, "ONLYA-001")
        _make(session, beta, "TB-002")
    tool = _tool(monkeypatch, factory, {"alpha": alpha[0], "beta": beta[0]})

    with pytest.raises(ValueError, match="ONLYA-001"):
        tool(project="beta", task_id="TB-002", blocker_id="ONLYA-001", note="чужой блокер")

    assert _edges(factory, beta[0]) == []
