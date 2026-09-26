"""AFT-006 (RFC 27 F7): MCP ``task_list`` — фильтры plan/section/type/даты/commit.

Параметры ``plan_scope``, ``section_letter``, ``type``, ``completed_since``,
``updated_since``, ``has_commit`` пробрасываются в ``task_service``
(``list_for_project`` и ``count_for_project`` — total совпадает с items),
ISO-строки разбираются через ``task_service.parse_since``, ``type`` — через
``TaskType``. Ошибки сервиса (``section_letter`` без ``plan_scope``, битая
дата) не глотаются. Строки несут ``plan_scope``/``section_letter`` из
сериализатора. Эталоны — литералы; строить ожидание вызовом
``task_service.list_for_project``/``task_to_dict``/``parse_since`` запрещено
спекой.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import Plan, PlanSection
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import TaskModel
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


def _dt(month: int, day: int, hour: int = 10) -> datetime:
    return datetime(2026, month, day, hour, 0, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _seed(session: Session) -> int:
    now = _dt(9, 1)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="pr", title="pr", root_path="/tmp/pr", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    plan_x = PlanRepository(session).add(
        Plan(project_id=proj.row_id, scope="plan-x", principle="test-first")
    )
    plan_x.created = now
    plan_x.last_updated = now
    session.flush()
    plan_y = PlanRepository(session).add(
        Plan(project_id=proj.row_id, scope="plan-y", principle="test-first")
    )
    plan_y.created = now
    plan_y.last_updated = now
    session.flush()
    sec_x_a = PlanSectionRepository(session).add(
        PlanSection(plan_id=plan_x.row_id, letter="A", title="Core", slug="A-Core", position=0)
    )
    sec_x_c = PlanSectionRepository(session).add(
        PlanSection(plan_id=plan_x.row_id, letter="C", title="Close", slug="C-Close", position=1)
    )
    sec_y_c = PlanSectionRepository(session).add(
        PlanSection(plan_id=plan_y.row_id, letter="C", title="Close", slug="C-Close", position=0)
    )
    session.flush()
    tasks = [
        # done, recent (>= 2026-09-16), with commit sha — the F7 headline query
        TaskModel(
            project_id=proj.row_id,
            task_id="AFT-006-001",
            plan_id=plan_x.row_id,
            section_id=sec_x_c.row_id,
            title="AFT-006 done recent with sha",
            status="done",
            type="bug",
            priority="high",
            created=_dt(9, 5),
            last_updated=_dt(9, 20),
            completed_at=_dt(9, 20),
            completed_commit="abc1234",
        ),
        # done, but older than 2026-09-16 — must not pass completed_since
        TaskModel(
            project_id=proj.row_id,
            task_id="AFT-006-002",
            plan_id=plan_x.row_id,
            section_id=sec_x_c.row_id,
            title="AFT-006 done old with sha",
            status="done",
            type="feature",
            priority="medium",
            created=_dt(9, 1),
            last_updated=_dt(9, 10),
            completed_at=_dt(9, 10),
            completed_commit="def5678",
        ),
        # open task of section C of plan-x
        TaskModel(
            project_id=proj.row_id,
            task_id="AFT-006-003",
            plan_id=plan_x.row_id,
            section_id=sec_x_c.row_id,
            title="AFT-006 open section C plan-x",
            status="todo",
            type="feature",
            priority="high",
            created=_dt(9, 15),
            last_updated=_dt(9, 22, 8),
        ),
        # open, but section A of plan-x
        TaskModel(
            project_id=proj.row_id,
            task_id="AFT-006-004",
            plan_id=plan_x.row_id,
            section_id=sec_x_a.row_id,
            title="AFT-006 open section A plan-x",
            status="todo",
            type="bug",
            priority="low",
            created=_dt(8, 20),
            last_updated=_dt(9, 1),
        ),
        # open section C, but of plan-y
        TaskModel(
            project_id=proj.row_id,
            task_id="AFT-006-005",
            plan_id=plan_y.row_id,
            section_id=sec_y_c.row_id,
            title="AFT-006 open section C plan-y",
            status="todo",
            type="feature",
            priority="medium",
            created=_dt(9, 18),
            last_updated=_dt(9, 23),
        ),
        # done and recent, but without commit — has_commit=True must drop it
        TaskModel(
            project_id=proj.row_id,
            task_id="AFT-006-006",
            plan_id=plan_x.row_id,
            section_id=sec_x_c.row_id,
            title="AFT-006 done recent no sha",
            status="done",
            type="bug",
            priority="medium",
            created=_dt(9, 12),
            last_updated=_dt(9, 21),
            completed_at=_dt(9, 21),
            completed_commit=None,
        ),
    ]
    session.add_all(tasks)
    session.flush()
    return proj.row_id


def _task_list(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> Any:
    from cod_doc.mcp.tools import task_tools

    factory = make_session_factory(engine)
    with transactional(factory) as s:
        proj_id = _seed(s)
    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    return mcp._tool_manager._tools["task_list"].fn


def test_done_recent_with_commit_one_call(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    task_list = _task_list(monkeypatch, engine)

    result = task_list(
        project="pr",
        status="done",
        completed_since="2026-09-16",
        has_commit=True,
    )

    assert [r["task_id"] for r in result["items"]] == ["AFT-006-001"]
    assert result["items"][0]["completed_commit"] == "abc1234"
    assert result["total"] == 1


def test_open_tasks_of_section_c_one_call(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    task_list = _task_list(monkeypatch, engine)

    result = task_list(
        project="pr",
        plan_scope="plan-x",
        section_letter="C",
        status="todo",
    )

    assert {r["task_id"] for r in result["items"]} == {"AFT-006-003"}
    assert result["total"] == 1
    for row in result["items"]:
        assert row["plan_scope"] == "plan-x"
        assert row["section_letter"] == "C"


def test_section_letter_without_plan_scope_is_error(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_list = _task_list(monkeypatch, engine)

    with pytest.raises(ValueError, match="plan_scope"):
        task_list(project="pr", section_letter="C")


def test_bad_iso_is_error(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    task_list = _task_list(monkeypatch, engine)

    with pytest.raises(ValueError):
        task_list(project="pr", completed_since="yesterday")


def test_type_and_updated_since_filters(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    task_list = _task_list(monkeypatch, engine)

    by_type = task_list(project="pr", type="bug", limit=50)
    assert {r["task_id"] for r in by_type["items"]} == {
        "AFT-006-001",
        "AFT-006-004",
        "AFT-006-006",
    }
    assert by_type["total"] == 3

    by_updated = task_list(project="pr", updated_since="2026-09-22", limit=50)
    assert {r["task_id"] for r in by_updated["items"]} == {
        "AFT-006-003",
        "AFT-006-005",
    }
    assert by_updated["total"] == 2


def test_rows_carry_plan_scope_without_filters(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_list = _task_list(monkeypatch, engine)

    result = task_list(project="pr", limit=50)

    expected = {
        "AFT-006-001": ("plan-x", "C"),
        "AFT-006-002": ("plan-x", "C"),
        "AFT-006-003": ("plan-x", "C"),
        "AFT-006-004": ("plan-x", "A"),
        "AFT-006-005": ("plan-y", "C"),
        "AFT-006-006": ("plan-x", "C"),
    }
    assert {r["task_id"] for r in result["items"]} == set(expected)
    for row in result["items"]:
        scope, letter = expected[row["task_id"]]
        assert row["plan_scope"] == scope
        assert row["section_letter"] == letter
