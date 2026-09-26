"""ADO-199: секция плана валидируется на записи, слаг даёт единый генератор.

Тулы plan_tools зовутся на свежем FastMCP с подменённым session_factory. Все
эталонные слаги — литералы: построить ожидаемое значение генератором значило
бы сравнить функцию саму с собой.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from cod_doc.domain.entities import Plan
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import PlanSectionModel
from cod_doc.infra.repositories import PlanRepository, ProjectRepository
from cod_doc.services.validation import ValidationError
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session, sessionmaker

_REPO = Path(__file__).resolve().parent.parent
_PLAN_SCOPE = "pv-plan"


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
    with transactional(factory) as s:
        proj = ProjectRepository(s).add(
            ProjectEntity(slug="pv", title="pv", root_path="/tmp/pv", config={})
        )
        proj.created = now
        proj.updated = now
        s.flush()
        plan = PlanRepository(s).add(
            Plan(project_id=proj.row_id, scope=_PLAN_SCOPE, principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        s.flush()
        proj_id = proj.row_id

    monkeypatch.setattr(plan_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(plan_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    plan_tools.register(mcp)
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


def _sections(factory: sessionmaker[Session]) -> list[tuple[str, str, str, int]]:
    with transactional(factory) as s:
        rows = s.execute(
            select(
                PlanSectionModel.letter,
                PlanSectionModel.title,
                PlanSectionModel.slug,
                PlanSectionModel.position,
            ).order_by(PlanSectionModel.row_id)
        ).all()
        return [(r[0], r[1], r[2], r[3]) for r in rows]


def _section_count(factory: sessionmaker[Session]) -> int:
    with transactional(factory) as s:
        return int(s.execute(select(func.count(PlanSectionModel.row_id))).scalar_one())


def test_section_create_default_slug(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    out = tools["plan_section_create"](
        project="pv", plan_scope=_PLAN_SCOPE, letter="F", title="Structure protocol (RFC 24)"
    )
    assert out["slug"] == "F-Structure-protocol-RFC-24"
    assert "warnings" not in out
    assert _sections(factory) == [
        ("F", "Structure protocol (RFC 24)", "F-Structure-protocol-RFC-24", 0)
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"letter": "A", "title": "Data Core", "position": -1},
        {"letter": "A", "title": ""},
        {"letter": "A", "title": "a\nb"},
        {"letter": "ABC", "title": "Data Core"},
        {"letter": "A", "title": "Data Core", "slug": "Structure protocol"},
    ],
)
def test_section_create_rejects_bad_input(
    tools: dict[str, Callable[..., Any]],
    factory: sessionmaker[Session],
    kwargs: dict[str, Any],
) -> None:
    tools["plan_section_create"](project="pv", plan_scope=_PLAN_SCOPE, letter="Z", title="Tail")
    assert _section_count(factory) == 1

    with pytest.raises(ValidationError):
        tools["plan_section_create"](project="pv", plan_scope=_PLAN_SCOPE, **kwargs)

    assert _section_count(factory) == 1


def test_section_create_explicit_valid_slug_kept(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    out = tools["plan_section_create"](
        project="pv", plan_scope=_PLAN_SCOPE, letter="b", title="Services", slug=" B-Svc "
    )
    assert (out["letter"], out["slug"]) == ("B", "B-Svc")
    assert _sections(factory) == [("B", "Services", "B-Svc", 0)]


def test_section_create_html_warning(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    out = tools["plan_section_create"](
        project="pv", plan_scope=_PLAN_SCOPE, letter="A", title="Q&amp;A"
    )
    assert out["title"] == "Q&amp;A"
    assert len(out["warnings"]) == 1
    assert "&amp;" in out["warnings"][0]
    assert _sections(factory)[0][1] == "Q&amp;A"

    clean = tools["plan_section_create"](
        project="pv", plan_scope=_PLAN_SCOPE, letter="B", title="Q&A"
    )
    assert "warnings" not in clean
    assert _sections(factory)[1][1] == "Q&A"


def test_plan_create_sections_use_generator(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    out = tools["plan_create"](
        project="pv", scope="pv-new", sections=[{"letter": "A", "title": "Data Core"}]
    )
    assert out["sections"][0]["slug"] == "A-Data-Core"
    assert "warnings" not in out
    assert _sections(factory) == [("A", "Data Core", "A-Data-Core", 0)]

    with pytest.raises(ValidationError):
        tools["plan_create"](
            project="pv",
            scope="pv-bad",
            sections=[{"letter": "A", "title": "Data Core", "position": -1}],
        )
    assert _section_count(factory) == 1


def test_plan_create_collects_html_warnings(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    out = tools["plan_create"](
        project="pv",
        scope="pv-warn",
        sections=[{"letter": "A", "title": "R&amp;D"}, {"letter": "B", "title": "Plain"}],
    )
    assert len(out["warnings"]) == 1
    assert "&amp;" in out["warnings"][0]
    assert [row[1] for row in _sections(factory)] == ["R&amp;D", "Plain"]


def test_legacy_slugs_still_readable(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    with transactional(factory) as s:
        plan = PlanRepository(s).get_by_scope(_PLAN_SCOPE)
        assert plan is not None
        s.add(
            PlanSectionModel(
                plan_id=plan.row_id,
                letter="F",
                title="Structure protocol (RFC 24)",
                slug="Structure protocol (RFC 24)",
                position=-1,
            )
        )

    listed = tools["plan_sections_list"](project="pv", plan_scope=_PLAN_SCOPE)
    assert [(r["letter"], r["slug"], r["position"]) for r in listed] == [
        ("F", "Structure protocol (RFC 24)", -1)
    ]
    progress = tools["plan_progress"](project="pv", plan_scope=_PLAN_SCOPE)
    assert [s["letter"] for s in progress["sections"]] == ["F"]


def _str_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_no_literal_slug_generators() -> None:
    pkg = _REPO / "cod_doc"
    plan_tools_src = (pkg / "mcp" / "tools" / "plan_tools.py").read_text(encoding="utf-8")
    assert "or title.strip()" not in plan_tools_src
    assert 'or spec["title"]' not in plan_tools_src

    offenders = [
        str(p.relative_to(_REPO)) for p in pkg.rglob("*.py") if "legacy-rest" in _str_constants(p)
    ]
    assert offenders == []

    importer = pkg / "services" / "restate_importer.py"
    assert "A-Imported" not in importer.read_text(encoding="utf-8")
