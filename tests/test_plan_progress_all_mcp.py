"""AFT-007 (RFC 27 F8): plan_progress / plan_sections_list без plan_scope.

Тулы зовутся на свежем FastMCP с подменённым session_factory; в одной БД два
проекта, ``alpha`` и ``beta``. Статусы задач пишутся прямо в ``TaskModel``,
эталоны — литералы сида. Единственное сравнение режимов между собой —
``test_all_mode_matches_single_plan``: совпадение all-режима с
``plan_progress(plan_scope=X)`` и есть предмет критерия приёмки 1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

# (project, scope, [(letter, [status, ...]), ...]) — порядок = порядок создания.
_SEED: list[tuple[str, str, list[tuple[str, list[str]]]]] = [
    ("alpha", "plan-x", [("A", ["done", "todo"]), ("B", ["cancelled"])]),
    ("alpha", "plan-y", [("C", ["in_progress"])]),
    ("beta", "plan-z", [("A", ["done"])]),
]


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=url)
    engine = make_engine(url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def tools(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> dict[str, Callable[..., Any]]:
    from cod_doc.mcp.tools import plan_tools

    now = datetime.now(UTC)
    ids: dict[str, int] = {}
    with transactional(factory) as s:
        for slug in ("alpha", "beta"):
            row = ProjectModel(
                slug=slug, title=slug, root_path=f"/tmp/{slug}", created=now, updated=now
            )
            s.add(row)
            s.flush()
            ids[slug] = row.row_id

        task_no = 0
        for project, scope, sections in _SEED:
            plan = PlanModel(
                project_id=ids[project],
                scope=scope,
                principle="from-rfc",
                created=now,
                last_updated=now,
            )
            s.add(plan)
            s.flush()
            for position, (letter, statuses) in enumerate(sections):
                sec = PlanSectionModel(
                    plan_id=plan.row_id,
                    letter=letter,
                    title=f"Section {letter}",
                    slug=f"{letter}-Section",
                    position=position,
                )
                s.add(sec)
                s.flush()
                for status in statuses:
                    task_no += 1
                    s.add(
                        TaskModel(
                            project_id=ids[project],
                            task_id=f"T-{task_no:03d}",
                            plan_id=plan.row_id,
                            section_id=sec.row_id,
                            title=f"task {task_no}",
                            status=status,
                            type="feature",
                            priority="medium",
                            created=now,
                            last_updated=now,
                        )
                    )

    monkeypatch.setattr(plan_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(plan_tools, "require_project_id", lambda session, project: ids[project])
    mcp = FastMCP("test")
    plan_tools.register(mcp)
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


_PLAN_KEYS = {"scope", "total", "done", "in_progress", "cancelled", "remaining", "status"}


def test_all_plans_by_section_one_call(tools: dict[str, Callable[..., Any]]) -> None:
    result = tools["plan_progress"](project="alpha", by_section=True)

    plans = result["plans"]
    assert [p["scope"] for p in plans] == ["plan-x", "plan-y"]
    x, y = plans
    assert [s["letter"] for s in x["sections"]] == ["A", "B"]
    sec_a, sec_b = x["sections"]
    assert (sec_a["total"], sec_a["done"], sec_a["remaining"]) == (2, 1, 1)
    assert (sec_b["total"], sec_b["cancelled"], sec_b["remaining"]) == (1, 1, 0)
    assert (x["total"], x["done"], x["cancelled"], x["remaining"]) == (3, 1, 1, 1)

    assert [s["letter"] for s in y["sections"]] == ["C"]
    (sec_c,) = y["sections"]
    assert (sec_c["total"], sec_c["done"], sec_c["remaining"]) == (1, 0, 1)
    assert (y["in_progress"], y["remaining"]) == (1, 1)


def test_all_mode_matches_single_plan(tools: dict[str, Callable[..., Any]]) -> None:
    result = tools["plan_progress"](project="alpha", by_section=True)

    for row in result["plans"]:
        single = tools["plan_progress"](project="alpha", plan_scope=row["scope"])
        assert set(row) == _PLAN_KEYS | {"sections"}
        assert row == single

    assert result["plans"][0]["total"] == 3


def test_all_mode_without_sections(tools: dict[str, Callable[..., Any]]) -> None:
    result = tools["plan_progress"](project="alpha")

    assert set(result) == {"project", "plans"}
    assert result["project"] == "alpha"
    assert [p["scope"] for p in result["plans"]] == ["plan-x", "plan-y"]
    for row in result["plans"]:
        assert "sections" not in row
        assert set(row) == _PLAN_KEYS


def test_single_plan_shape_unchanged(tools: dict[str, Callable[..., Any]]) -> None:
    result = tools["plan_progress"](project="alpha", plan_scope="plan-x")

    assert set(result) == {
        "scope",
        "total",
        "done",
        "in_progress",
        "cancelled",
        "remaining",
        "status",
        "sections",
    }


def test_unknown_scope_lists_available(tools: dict[str, Callable[..., Any]]) -> None:
    expected = "Plan 'nope' not found. Available plan scopes: plan-x, plan-y"
    for name in ("plan_progress", "plan_sections_list", "plan_ready"):
        with pytest.raises(ValueError) as excinfo:
            tools[name](project="alpha", plan_scope="nope")
        assert str(excinfo.value) == expected, name


def test_foreign_plan_not_found(tools: dict[str, Callable[..., Any]]) -> None:
    with pytest.raises(ValueError) as excinfo:
        tools["plan_progress"](project="alpha", plan_scope="plan-z")

    message = str(excinfo.value)
    assert message == "Plan 'plan-z' not found. Available plan scopes: plan-x, plan-y"
    available = message.split("Available plan scopes: ", 1)[1].split(", ")
    assert available == ["plan-x", "plan-y"]
    assert "plan-z" not in available


def test_sections_list_without_scope(tools: dict[str, Callable[..., Any]]) -> None:
    rows = tools["plan_sections_list"](project="alpha")

    assert [(r["plan_scope"], r["letter"]) for r in rows] == [
        ("plan-x", "A"),
        ("plan-x", "B"),
        ("plan-y", "C"),
    ]
    sec_a = rows[0]
    assert (sec_a["task_count"], sec_a["done_count"]) == (2, 1)
    assert (rows[1]["task_count"], rows[1]["done_count"]) == (1, 0)
    assert (rows[2]["task_count"], rows[2]["done_count"]) == (1, 0)
    assert all(r["plan_scope"] != "plan-z" for r in rows)


def test_sections_list_with_scope_adds_plan_scope(tools: dict[str, Callable[..., Any]]) -> None:
    rows = tools["plan_sections_list"](project="alpha", plan_scope="plan-x")

    assert [(r["plan_scope"], r["letter"], r["task_count"]) for r in rows] == [
        ("plan-x", "A", 2),
        ("plan-x", "B", 1),
    ]
