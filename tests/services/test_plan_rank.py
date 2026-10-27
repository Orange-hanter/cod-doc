"""ROAD-001/002 (RFC 36 §3.2.2, §3.6): порядок планов — ``plan.rank`` и ``move_plan``.

Контракты RFC 36 §3.7: C-05 (``plan_move`` на MCP и CLI — остальное держит
``test_plan_mutation_surface_parity.py``), C-06 (отказы без записи), C-12 (нет
колонок с плановой датой), C-13 (ранг у каждого плана, новый — последний).

Эталоны — литералы и прямые SELECT по ``plan``, ``revision``,
``activity_event``; ожидаемый порядок не строится вызовом кода под тестом.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from cod_doc.cli import main
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, PlanModel, ProjectModel, RevisionModel
from cod_doc.infra.models.base import Base
from cod_doc.services import plan_service
from cod_doc.services.plan_service import PlanNotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

AUTHOR = "agent:dakh/claude-code"
_SCOPES = ("alpha", "beta", "gamma", "delta")

#: C-12: подстроки имён, которые выдают колонку с плановой датой.
_PLANNED_DATE_MARKERS = ("due", "deadline", "target", "planned")


@pytest.fixture
def sf(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _project(session: Session, slug: str) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


@pytest.fixture
def pids(sf: sessionmaker[Session]) -> dict[str, int]:
    """Два проекта; в ``one`` — четыре плана по порядку создания, в ``two`` — один."""
    with transactional(sf) as s:
        out = {"one": _project(s, "one"), "two": _project(s, "two")}
        for scope in _SCOPES:
            plan_service.create_plan(
                s, project_id=out["one"], scope=scope, principle=None, author=AUTHOR
            )
        plan_service.create_plan(
            s, project_id=out["two"], scope="foreign", principle=None, author=AUTHOR
        )
    return out


def _order(sf: sessionmaker[Session], project_id: int) -> list[str]:
    with transactional(sf) as s:
        return list(
            s.scalars(
                select(PlanModel.scope)
                .where(PlanModel.project_id == project_id)
                .order_by(PlanModel.rank, PlanModel.uid, PlanModel.row_id)
            )
        )


def _ranks(sf: sessionmaker[Session], project_id: int) -> dict[str, str | None]:
    with transactional(sf) as s:
        rows = s.execute(
            select(PlanModel.scope, PlanModel.rank).where(PlanModel.project_id == project_id)
        ).all()
    return {scope: rank for scope, rank in rows}


def _writes(sf: sessionmaker[Session]) -> tuple[int, int]:
    """(ревизии плана, события plan.moved) — счётчики для проверки «без записи»."""
    with transactional(sf) as s:
        revisions = s.scalar(
            select(func.count(RevisionModel.row_id)).where(RevisionModel.entity_kind == "plan")
        )
        events = s.scalar(
            select(func.count(ActivityEventModel.row_id)).where(
                ActivityEventModel.kind == "plan.moved"
            )
        )
    return int(revisions or 0), int(events or 0)


def _move(sf: sessionmaker[Session], project_id: int, scope: str, **where: str) -> list[str]:
    with transactional(sf) as s:
        ordered = plan_service.move_plan(
            s, project_id=project_id, plan_scope=scope, author=AUTHOR, **where
        )
        return [p.scope for p in ordered]


# --- C-13: ранг есть у каждого плана, новый план — последний -----------------------


def test_new_plans_get_ranks_in_creation_order(
    sf: sessionmaker[Session], pids: dict[str, int]
) -> None:
    assert all(_ranks(sf, pids["one"]).values())
    assert _order(sf, pids["one"]) == list(_SCOPES)


def test_new_plan_goes_last_after_a_move(sf: sessionmaker[Session], pids: dict[str, int]) -> None:
    _move(sf, pids["one"], "delta", before="alpha")
    with transactional(sf) as s:
        plan_service.create_plan(
            s, project_id=pids["one"], scope="epsilon", principle=None, author=AUTHOR
        )
    assert _order(sf, pids["one"]) == ["delta", "alpha", "beta", "gamma", "epsilon"]


def test_list_for_project_follows_rank(sf: sessionmaker[Session], pids: dict[str, int]) -> None:
    _move(sf, pids["one"], "gamma", before="alpha")
    with transactional(sf) as s:
        listed = [p.scope for p in plan_service.list_for_project(s, pids["one"])]
        summary = [row["scope"] for row in plan_service.list_plans_summary(s, pids["one"])]
    assert listed == ["gamma", "alpha", "beta", "delta"]
    assert summary == listed


# --- move_plan --------------------------------------------------------------------


def test_move_after_changes_only_the_moved_plan(
    sf: sessionmaker[Session], pids: dict[str, int]
) -> None:
    before = _ranks(sf, pids["one"])
    order = _move(sf, pids["one"], "alpha", after="gamma")
    after = _ranks(sf, pids["one"])

    assert order == ["beta", "gamma", "alpha", "delta"]
    assert _order(sf, pids["one"]) == order
    assert {s for s in _SCOPES if before[s] != after[s]} == {"alpha"}
    assert _ranks(sf, pids["two"])["foreign"] is not None


def test_move_writes_one_revision_and_one_event(
    sf: sessionmaker[Session], pids: dict[str, int]
) -> None:
    revisions, events = _writes(sf)
    _move(sf, pids["one"], "delta", before="beta")
    assert _writes(sf) == (revisions + 1, events + 1)


def test_move_in_place_writes_nothing(sf: sessionmaker[Session], pids: dict[str, int]) -> None:
    revisions, events = _writes(sf)
    ranks = _ranks(sf, pids["one"])
    assert _move(sf, pids["one"], "beta", after="alpha") == list(_SCOPES)
    assert _writes(sf) == (revisions, events)
    assert _ranks(sf, pids["one"]) == ranks


# --- C-06: отказы без ревизии и события ---------------------------------------------


@pytest.mark.parametrize(
    ("scope", "where", "error"),
    [
        ("alpha", {}, ValueError),
        ("alpha", {"before": "beta", "after": "gamma"}, ValueError),
        ("alpha", {"before": "alpha"}, ValueError),
        ("alpha", {"before": "missing"}, PlanNotFoundError),
        ("alpha", {"before": "foreign"}, PlanNotFoundError),
        ("missing", {"before": "alpha"}, PlanNotFoundError),
    ],
    ids=["neither", "both", "itself", "unknown-anchor", "foreign-anchor", "unknown-plan"],
)
def test_refused_moves_write_nothing(
    sf: sessionmaker[Session],
    pids: dict[str, int],
    scope: str,
    where: dict[str, str],
    error: type[Exception],
) -> None:
    writes, ranks = _writes(sf), _ranks(sf, pids["one"])
    with pytest.raises(error):
        _move(sf, pids["one"], scope, **where)
    assert _writes(sf) == writes
    assert _ranks(sf, pids["one"]) == ranks


# --- C-05: поверхности ------------------------------------------------------------


@pytest.fixture
def tools(
    monkeypatch: pytest.MonkeyPatch, sf: sessionmaker[Session], pids: dict[str, int]
) -> dict[str, Callable[..., Any]]:
    from cod_doc.mcp.tools import plan_tools

    monkeypatch.setattr(plan_tools, "session_factory", lambda project: (sf, None))
    monkeypatch.setattr(plan_tools, "require_project_id", lambda session, project: pids[project])
    mcp = FastMCP("test")
    plan_tools.register(mcp)
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


def test_mcp_plan_move(
    tools: dict[str, Callable[..., Any]], sf: sessionmaker[Session], pids: dict[str, int]
) -> None:
    out = tools["plan_move"](project="one", plan_scope="gamma", before="alpha", author=AUTHOR)
    assert out == {"scope": "gamma", "order": ["gamma", "alpha", "beta", "delta"]}
    assert [row["scope"] for row in tools["plan_list"](project="one")] == out["order"]


def test_cli_plan_move(tmp_path: Path) -> None:
    def invoke(args: list[str]) -> str:
        result = CliRunner().invoke(main, args, env={"FORCE_COLOR": ""})
        assert result.exit_code == 0, result.output
        return result.output

    root = tmp_path / "proj"
    root.mkdir()
    invoke(["project", "add", str(root), "--name", "proj"])
    for scope in ("first", "second", "third"):
        invoke(["plan", "create", scope, "-p", "proj", "--principle", "P"])

    moved = json.loads(
        invoke(["plan", "move", "third", "-p", "proj", "--before", "first", "--json"])
    )
    listed = json.loads(invoke(["plan", "list", "-p", "proj", "--json"]))

    assert moved == {"scope": "third", "order": ["third", "first", "second"]}
    assert [row["scope"] for row in listed] == moved["order"]


def test_cli_plan_move_refusal_has_no_traceback(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    runner = CliRunner()
    env = {"FORCE_COLOR": ""}
    assert (
        runner.invoke(main, ["project", "add", str(root), "--name", "proj"], env=env).exit_code == 0
    )
    assert (
        runner.invoke(
            main, ["plan", "create", "only", "-p", "proj", "--principle", "P"], env=env
        ).exit_code
        == 0
    )
    result = runner.invoke(main, ["plan", "move", "only", "-p", "proj"], env=env)
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "exactly one of before, after" in result.output


# --- C-12: колонок с плановой датой нет ---------------------------------------------


def test_no_planned_date_columns() -> None:
    """Дата на дорожной карте — прогноз и не хранится (RFC 36 §3.1, §3.2)."""
    offenders = [
        f"{table}.{column.name}"
        for table in ("plan", "task", "milestone")
        if table in Base.metadata.tables
        for column in Base.metadata.tables[table].columns
        if any(marker in column.name for marker in _PLANNED_DATE_MARKERS)
    ]
    assert offenders == []
