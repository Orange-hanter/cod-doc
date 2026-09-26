"""AFT-011: task_create_many — конфликты ID по элементу, created ⊆ БД."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.mcp.tools import task_tools
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield make_session_factory(engine)
    engine.dispose()


def _seed(factory: sessionmaker[Session], existing_task_id: str) -> None:
    now = datetime.now(UTC)
    with transactional(factory) as session:
        proj = ProjectModel(slug="ado", title="P", root_path="/tmp/p", config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()
        plan = PlanModel(
            project_id=proj.row_id, scope="adoption-2026-08", created=now, last_updated=now
        )
        session.add(plan)
        session.flush()
        sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
        session.add(sec)
        session.flush()
        session.add(
            TaskModel(
                project_id=proj.row_id,
                plan_id=plan.row_id,
                section_id=sec.row_id,
                task_id=existing_task_id,
                title="Existing",
                status="todo",
                type="feature",
                priority="medium",
                created=now,
                last_updated=now,
            )
        )


def _create_many(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    items: list[dict[str, Any]],
    **kw: Any,
) -> dict[str, Any]:
    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    fn = mcp._tool_manager._tools["task_create_many"].fn
    return fn(project="ado", plan_scope="adoption-2026-08", section_letter="A", items=items, **kw)  # type: ignore[no-any-return]


def _task_ids(factory: sessionmaker[Session]) -> set[str]:
    with transactional(factory) as session:
        return set(session.execute(select(TaskModel.task_id)).scalars())


_CONFLICT_BATCH = [
    {"title": "First", "task_id": "ADO-010"},
    {"title": "Taken", "task_id": "ADO-002"},
    {"title": "Third", "task_id": "ADO-011"},
]


def test_continue_on_error_one_conflict(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(factory, "ADO-002")
    result = _create_many(factory, monkeypatch, _CONFLICT_BATCH, continue_on_error=True)

    assert [c["task_id"] for c in result["created"]] == ["ADO-010", "ADO-011"]
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err["index"] == 1
    assert err["code"] == "conflict"
    assert err["next_free_id"] == "ADO-012"
    assert result["committed"] is True
    assert {"ADO-010", "ADO-011"} <= _task_ids(factory)


def test_without_flag_rolls_back_and_created_empty(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(factory, "ADO-002")
    result = _create_many(factory, monkeypatch, _CONFLICT_BATCH)

    assert result["committed"] is False
    assert result["created"] == []
    assert result["errors"][0]["index"] == 1
    assert result["errors"][0]["code"] == "conflict"
    ids = _task_ids(factory)
    assert "ADO-010" not in ids
    assert "ADO-011" not in ids


def test_auto_ids_in_batch(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(factory, "ADO-005")
    result = _create_many(
        factory,
        monkeypatch,
        [{"title": "One"}, {"title": "Two"}, {"title": "Three"}],
    )

    assert result["committed"] is True
    assert [c["task_id"] for c in result["created"]] == ["ADO-006", "ADO-007", "ADO-008"]


def test_error_message_has_no_sql(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(factory, "ADO-002")
    result = _create_many(factory, monkeypatch, _CONFLICT_BATCH, continue_on_error=True)

    assert result["errors"]
    for err in result["errors"]:
        assert "INSERT" not in err["message"]
        assert "sqlite3" not in err["message"]
