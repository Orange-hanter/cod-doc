"""ADO-226: ответы мутационных тулов несут поля task_get (находка N3 agent-fit).

``task_complete`` / ``task_update_status`` / ``task_set_blocker`` /
``task_clear_blocker`` / ``task_remove_dependency`` / ``task_add_dependency``
сериализуют задачу через ``task_to_dict(t, session=session)`` внутри
транзакции, поэтому plan_scope / section_letter / blocked_by / affects_files /
story_id заполнены, как в ``task_get``.

Тулы регистрируются на свежем FastMCP, ``session_factory`` и
``require_project_id`` подменяются через monkeypatch (образец —
``tests/test_task_add_dependency_mcp.py``). Эталоны — литералы сида и прямые
SELECT; ожидание не строится вызовом ``task_to_dict`` / ``task_service``
(исключение — ``test_mutation_response_matches_task_get``, где совпадение с
``task_get`` и есть предмет проверки).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import event, select, update

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services import task_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

PROJECT = "alpha"
PLAN_SCOPE = "plan-x"
SECTION_LETTER = "C"
AFFECTED = ["cod_doc/x.py"]

# Мутационные тулы, чей ответ обязан совпадать по форме с task_get.
MUTATION_TOOLS = (
    "task_complete",
    "task_update_status",
    "task_set_blocker",
    "task_clear_blocker",
    "task_remove_dependency",
)


@pytest.fixture
def engine_with_schema(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed(engine: Engine) -> tuple[sessionmaker[Session], int]:
    """Проект, план 'plan-x' с секцией 'C' и задачи TA-001…TA-005.

    TA-002 — блокер со статусом done (выставлен прямо в TaskModel);
    TA-004 — блокер в todo; TA-001/TA-003/TA-005 несут
    affected_files=['cod_doc/x.py'] и blocked_by по спецификации ADO-226.
    """
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectModel(slug=PROJECT, title=PROJECT, root_path="/tmp/alpha", config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()
        plan = PlanModel(project_id=proj.row_id, scope=PLAN_SCOPE, created=now, last_updated=now)
        session.add(plan)
        session.flush()
        sec = PlanSectionModel(
            plan_id=plan.row_id, letter=SECTION_LETTER, title="Core", slug="C-Core", position=0
        )
        session.add(sec)
        session.flush()
        for tid, blocked_by, files in (
            ("TA-002", None, None),
            ("TA-004", None, None),
            ("TA-001", ["TA-002"], AFFECTED),
            ("TA-003", ["TA-002"], AFFECTED),
            ("TA-005", ["TA-002", "TA-004"], AFFECTED),
        ):
            task_service.create(
                session,
                project_id=proj.row_id,
                plan_id=plan.row_id,
                section_id=sec.row_id,
                task_id=tid,
                title=f"Implement: {tid}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
                affected_files=files,
                blocked_by=blocked_by,
            )
        session.execute(
            update(TaskModel)
            .where(TaskModel.project_id == proj.row_id, TaskModel.task_id == "TA-002")
            .values(status="done")
        )
        project_id = proj.row_id
    return factory, project_id


def _tools(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    project_id: int,
) -> dict[str, Callable[..., dict[str, Any]]]:
    from cod_doc.mcp.tools import task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: project_id)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    return {
        name: mcp._tool_manager._tools[name].fn
        for name in (*MUTATION_TOOLS, "task_add_dependency", "task_get")
    }


def _status(factory: sessionmaker[Session], project_id: int, task_id: str) -> str:
    with transactional(factory) as session:
        return session.execute(
            select(TaskModel.status).where(
                TaskModel.project_id == project_id, TaskModel.task_id == task_id
            )
        ).scalar_one()


def test_complete_response_carries_relations(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema)
    tools = _tools(monkeypatch, factory, pid)

    out = tools["task_complete"](project=PROJECT, task_id="TA-003", commit_sha="abc1234")

    assert out["plan_scope"] == PLAN_SCOPE
    assert out["section_letter"] == SECTION_LETTER
    assert out["affects_files"] == AFFECTED
    assert out["blocked_by"] == ["TA-002"]
    assert out["status"] == "done"


def test_update_status_response_carries_relations(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema)
    tools = _tools(monkeypatch, factory, pid)

    dry = tools["task_update_status"](
        project=PROJECT, task_id="TA-003", new_status="blocked", dry_run=True
    )
    assert dry["dry_run"] is True
    assert dry["plan_scope"] == PLAN_SCOPE
    assert dry["section_letter"] == SECTION_LETTER
    assert dry["affects_files"] == AFFECTED
    assert dry["blocked_by"] == ["TA-002"]
    assert _status(factory, pid, "TA-003") == "todo"

    out = tools["task_update_status"](project=PROJECT, task_id="TA-003", new_status="blocked")
    assert "dry_run" not in out
    assert out["plan_scope"] == PLAN_SCOPE
    assert out["section_letter"] == SECTION_LETTER
    assert out["affects_files"] == AFFECTED
    assert out["blocked_by"] == ["TA-002"]
    assert out["status"] == "blocked"


def test_set_and_clear_blocker_response_carries_relations(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema)
    tools = _tools(monkeypatch, factory, pid)

    blocked = tools["task_set_blocker"](project=PROJECT, task_id="TA-001", reason="ждём API")
    assert blocked["plan_scope"] == PLAN_SCOPE
    assert blocked["section_letter"] == SECTION_LETTER
    assert blocked["affects_files"] == AFFECTED
    assert blocked["blocked_by"] == ["TA-002"]
    assert blocked["blocked_reason"] == "ждём API"

    cleared = tools["task_clear_blocker"](project=PROJECT, task_id="TA-001")
    assert cleared["plan_scope"] == PLAN_SCOPE
    assert cleared["section_letter"] == SECTION_LETTER
    assert cleared["affects_files"] == AFFECTED
    assert cleared["blocked_by"] == ["TA-002"]
    assert cleared["blocked_reason"] is None


def test_remove_dependency_response_reflects_new_state(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema)
    tools = _tools(monkeypatch, factory, pid)

    out = tools["task_remove_dependency"](project=PROJECT, task_id="TA-005", blocker_id="TA-004")

    assert out["blocked_by"] == ["TA-002"]
    assert out["affects_files"] == AFFECTED
    assert out["plan_scope"] == PLAN_SCOPE
    assert out["section_letter"] == SECTION_LETTER


def test_add_dependency_response_carries_relations(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema)
    tools = _tools(monkeypatch, factory, pid)

    out = tools["task_add_dependency"](
        project=PROJECT, task_id="TA-001", blocker_id="TA-004", note="x"
    )

    assert out["blocked_by"] == ["TA-002", "TA-004"]
    assert out["plan_scope"] == PLAN_SCOPE
    assert out["op"] == "add_dependency"


def test_mutation_response_matches_task_get(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema)
    tools = _tools(monkeypatch, factory, pid)

    get = tools["task_get"]
    mutations: list[tuple[str, dict[str, Any], str]] = [
        ("task_complete", {"task_id": "TA-003", "commit_sha": "abc1234"}, "TA-003"),
        ("task_set_blocker", {"task_id": "TA-001", "reason": "ждём API"}, "TA-001"),
        ("task_clear_blocker", {"task_id": "TA-001"}, "TA-001"),
        ("task_update_status", {"task_id": "TA-001", "new_status": "blocked"}, "TA-001"),
        ("task_remove_dependency", {"task_id": "TA-005", "blocker_id": "TA-004"}, "TA-005"),
    ]
    for name, kwargs, task_id in mutations:
        out = tools[name](project=PROJECT, **kwargs)
        ref = get(project=PROJECT, task_id=task_id)
        assert set(out) - {"dry_run"} == set(ref), name
        for field in ("plan_scope", "section_letter", "blocked_by", "affects_files", "story_id"):
            assert out[field] == ref[field], (name, field)
        assert out["plan_scope"] == PLAN_SCOPE, name


def _relation_selects(engine: Engine, fn: Callable[[], Any]) -> dict[str, int]:
    """SELECT'ы, упоминающие affected_file / story_link, за время вызова fn."""
    counts = {"affected_file": 0, "story_link": 0}

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: Any
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            for table in counts:
                if table in statement:
                    counts[table] += 1

    event.listen(engine, "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return counts


def test_query_count_not_above_task_get(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    factory, pid = _seed(engine_with_schema)
    tools = _tools(monkeypatch, factory, pid)
    engine = engine_with_schema

    ref = _relation_selects(engine, lambda: tools["task_get"](project=PROJECT, task_id="TA-001"))
    assert ref == {"affected_file": 1, "story_link": 1}

    mutations: list[tuple[str, dict[str, Any]]] = [
        ("task_complete", {"task_id": "TA-003", "commit_sha": "abc1234"}),
        ("task_set_blocker", {"task_id": "TA-001", "reason": "ждём API"}),
        ("task_clear_blocker", {"task_id": "TA-001"}),
        ("task_update_status", {"task_id": "TA-001", "new_status": "blocked"}),
        ("task_remove_dependency", {"task_id": "TA-005", "blocker_id": "TA-004"}),
    ]
    for name, kwargs in mutations:
        call = tools[name]
        counts = _relation_selects(
            engine, lambda call=call, kwargs=kwargs: call(project=PROJECT, **kwargs)
        )
        assert counts == {"affected_file": 1, "story_link": 1}, name
        assert counts["affected_file"] <= ref["affected_file"], name
        assert counts["story_link"] <= ref["story_link"], name
