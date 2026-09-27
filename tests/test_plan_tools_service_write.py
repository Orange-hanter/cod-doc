"""ADO-203: plan_create и plan_section_create пишут через plan_service.

Тулы зовутся на свежем FastMCP с подменённым session_factory; в одной БД два
проекта, ``alpha`` и ``beta``. Все эталоны — литералы и прямые SELECT по
plan / plan_section / revision / activity_event: построить ожидание вызовом
сервиса значило бы сравнить функцию саму с собой.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
)
from cod_doc.services.plan_service import PlanNotFoundError
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session, sessionmaker

_PLAN_TOOLS = Path(__file__).resolve().parent.parent / "cod_doc" / "mcp" / "tools" / "plan_tools.py"


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

    monkeypatch.setattr(plan_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(plan_tools, "require_project_id", lambda session, project: ids[project])
    mcp = FastMCP("test")
    plan_tools.register(mcp)
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


def _count(factory: sessionmaker[Session], stmt: Any) -> int:
    with transactional(factory) as s:
        return int(s.execute(stmt).scalar_one())


def _revisions(factory: sessionmaker[Session], kind: str) -> list[tuple[int, str, str | None]]:
    with transactional(factory) as s:
        rows = s.execute(
            select(RevisionModel.entity_id, RevisionModel.author, RevisionModel.reason)
            .where(RevisionModel.entity_kind == kind)
            .order_by(RevisionModel.row_id)
        ).all()
        return [(r[0], r[1], r[2]) for r in rows]


def _events(factory: sessionmaker[Session], kind: str) -> list[str | None]:
    with transactional(factory) as s:
        return list(
            s.execute(
                select(ActivityEventModel.actor_id)
                .where(ActivityEventModel.kind == kind)
                .order_by(ActivityEventModel.row_id)
            ).scalars()
        )


def _section_count(factory: sessionmaker[Session]) -> int:
    return _count(factory, select(func.count(PlanSectionModel.row_id)))


def _seed_plan(tools: dict[str, Callable[..., Any]]) -> None:
    tools["plan_create"](project="alpha", scope="plan-x")


def test_no_repository_add_left() -> None:
    tree = ast.parse(_PLAN_TOOLS.read_text(encoding="utf-8"))
    add_calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add"
    ]
    assert add_calls == []

    plan_create = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "plan_create"
    )
    names = {n.id for n in ast.walk(plan_create) if isinstance(n, ast.Name)}
    aliases = {
        a.name for n in ast.walk(plan_create) if isinstance(n, ast.ImportFrom) for a in n.names
    }
    assert "PlanRepository" not in names
    assert "PlanRepository" not in aliases


def test_section_create_response_shape_unchanged(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    _seed_plan(tools)
    out = tools["plan_section_create"](
        project="alpha", plan_scope="plan-x", letter="F", title="Structure protocol (RFC 24)"
    )
    assert set(out) == {"section_id", "plan_scope", "letter", "title", "slug", "position"}
    assert out["slug"] == "F-Structure-protocol-RFC-24"
    assert out["position"] == 0
    assert out["plan_scope"] == "plan-x"
    assert out["letter"] == "F"
    assert out["title"] == "Structure protocol (RFC 24)"
    with transactional(factory) as s:
        row_id = s.execute(
            select(PlanSectionModel.row_id).where(PlanSectionModel.letter == "F")
        ).scalar_one()
    assert out["section_id"] == row_id


def test_section_create_writes_revision_and_event(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    _seed_plan(tools)
    out = tools["plan_section_create"](
        project="alpha", plan_scope="plan-x", letter="F", title="Structure protocol (RFC 24)"
    )
    revisions = _revisions(factory, "plan_section")
    assert len(revisions) == 1
    assert revisions[0][0] == out["section_id"]
    assert revisions[0][1] == "mcp"
    assert _events(factory, "plan.section_created") == ["mcp"]


def test_plan_create_response_and_trail(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    out = tools["plan_create"](
        project="alpha",
        scope="plan-x",
        sections=[{"letter": "A", "title": "Data Core"}, {"letter": "B", "title": "Q&amp;A"}],
    )
    assert set(out) == {"plan_id", "scope", "principle", "sections", "warnings"}
    assert out["scope"] == "plan-x"
    assert out["principle"] == "from-rfc"
    for sec in out["sections"]:
        assert set(sec) == {"section_id", "letter", "title", "slug", "position"}
    assert out["sections"][0]["slug"] == "A-Data-Core"
    assert [s["position"] for s in out["sections"]] == [0, 1]
    assert out["sections"][1]["title"] == "Q&amp;A"
    assert len(out["warnings"]) == 1
    assert "&amp;" in out["warnings"][0]

    with transactional(factory) as s:
        plan_id = s.execute(
            select(PlanModel.row_id).where(PlanModel.scope == "plan-x")
        ).scalar_one()
        section_ids = list(
            s.execute(select(PlanSectionModel.row_id).order_by(PlanSectionModel.position)).scalars()
        )
    assert out["plan_id"] == plan_id
    assert [s["section_id"] for s in out["sections"]] == section_ids

    plan_revs = _revisions(factory, "plan")
    assert len(plan_revs) == 1
    assert plan_revs[0][0] == plan_id
    section_revs = _revisions(factory, "plan_section")
    assert sorted(r[0] for r in section_revs) == sorted(section_ids)
    assert _events(factory, "plan.created") == ["mcp"]
    assert _events(factory, "plan.section_created") == ["mcp", "mcp"]


def test_warnings_kept_and_absent_when_clean(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    _seed_plan(tools)
    out = tools["plan_section_create"](
        project="alpha", plan_scope="plan-x", letter="A", title="Q&amp;A"
    )
    assert out["title"] == "Q&amp;A"
    assert len(out["warnings"]) == 1
    assert "&amp;" in out["warnings"][0]
    with transactional(factory) as s:
        stored = s.execute(
            select(PlanSectionModel.title).where(PlanSectionModel.letter == "A")
        ).scalar_one()
    assert stored == "Q&amp;A"

    clean = tools["plan_section_create"](
        project="alpha", plan_scope="plan-x", letter="B", title="Q&A"
    )
    assert "warnings" not in clean

    plan = tools["plan_create"](
        project="alpha", scope="plan-y", sections=[{"letter": "A", "title": "Data Core"}]
    )
    assert "warnings" not in plan


def test_dry_run_leaves_nothing(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    _seed_plan(tools)
    out = tools["plan_section_create"](
        project="alpha", plan_scope="plan-x", letter="F", title="Tail", dry_run=True
    )
    assert out["dry_run"] is True
    assert out["section_id"]
    assert out["slug"] == "F-Tail"

    assert _section_count(factory) == 0
    assert _revisions(factory, "plan_section") == []
    assert _events(factory, "plan.section_created") == []


def test_duplicate_letter_message_verbatim(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    _seed_plan(tools)
    tools["plan_section_create"](project="alpha", plan_scope="plan-x", letter="F", title="Tail")
    before = _count(factory, select(func.count(RevisionModel.row_id)))

    with pytest.raises(ValueError) as exc:
        tools["plan_section_create"](
            project="alpha", plan_scope="plan-x", letter="f", title="Other"
        )
    assert str(exc.value) == "Section letter 'f' already exists in plan 'plan-x'."
    assert _count(factory, select(func.count(RevisionModel.row_id))) == before


def test_duplicate_plan_scope_message(tools: dict[str, Callable[..., Any]]) -> None:
    _seed_plan(tools)
    with pytest.raises(ValueError) as exc:
        tools["plan_create"](project="alpha", scope="plan-x")
    assert str(exc.value) == "Plan with scope 'plan-x' already exists."


def test_reason_optional_and_author_default(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    for name in ("plan_create", "plan_section_create"):
        params = inspect.signature(tools[name]).parameters
        assert params["reason"].default is None
        assert params["author"].default == "mcp"

    tools["plan_create"](project="alpha", scope="plan-x", reason="seed")
    tools["plan_section_create"](
        project="alpha", plan_scope="plan-x", letter="A", title="Data Core", reason="seed"
    )
    assert [r[2] for r in _revisions(factory, "plan")] == ["seed"]
    assert [r[2] for r in _revisions(factory, "plan_section")] == ["seed"]


def test_foreign_project_plan_not_found(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    _seed_plan(tools)
    with pytest.raises(PlanNotFoundError):
        tools["plan_section_create"](
            project="beta", plan_scope="plan-x", letter="A", title="Data Core"
        )
    assert _section_count(factory) == 0
