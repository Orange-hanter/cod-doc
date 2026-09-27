"""ADO-203: MCP-тулы ``plan_section_update`` / ``_move`` / ``_delete`` поверх plan_service.

Тулы регистрируются на свежем FastMCP, ``session_factory`` и
``require_project_id`` подменяются (образец —
``tests/test_plan_section_write_validation.py``). Секции A–D заводятся тулом
``plan_section_create``, задачи — ``task_service.create``. Эталоны — литералы и
прямые SELECT по ``plan_section``, ``task``, ``revision``, ``activity_event``;
ожидание не строится вызовом ``update_section`` / ``move_section`` /
``delete_section``.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
    TaskModel,
)
from cod_doc.services import task_service
from cod_doc.services.plan_service import (
    PlanNotFoundError,
    SectionHasTasksError,
    SectionNotFoundError,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

_SCOPE = "alpha-plan"
_NEW_TOOLS = ("plan_section_update", "plan_section_move", "plan_section_delete")


@pytest.fixture
def engine_with_schema(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine_with_schema: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine_with_schema)


@pytest.fixture
def pids(factory: sessionmaker[Session]) -> dict[str, int]:
    now = datetime.now(UTC)
    out: dict[str, int] = {}
    with transactional(factory) as session:
        for slug in ("alpha", "beta"):
            proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
            proj.created = now
            proj.updated = now
            session.add(proj)
            session.flush()
            out[slug] = proj.row_id
        session.add(PlanModel(project_id=out["alpha"], scope=_SCOPE, created=now, last_updated=now))
    return out


@pytest.fixture
def tools(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session], pids: dict[str, int]
) -> dict[str, Callable[..., Any]]:
    from cod_doc.mcp.tools import plan_tools

    monkeypatch.setattr(plan_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(plan_tools, "require_project_id", lambda session, project: pids[project])
    mcp = FastMCP("test")
    plan_tools.register(mcp)
    fns = {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}
    for letter, title in (("A", "Alpha"), ("B", "Beta"), ("C", "Gamma"), ("D", "Delta")):
        fns["plan_section_create"](project="alpha", plan_scope=_SCOPE, letter=letter, title=title)
    return fns


def _sections(factory: sessionmaker[Session]) -> list[tuple[str, str, str, int]]:
    with transactional(factory) as s:
        rows = s.execute(
            select(
                PlanSectionModel.letter,
                PlanSectionModel.title,
                PlanSectionModel.slug,
                PlanSectionModel.position,
            ).order_by(PlanSectionModel.letter)
        ).all()
        return [(r[0], r[1], r[2], r[3]) for r in rows]


def _section_id(factory: sessionmaker[Session], letter: str) -> int:
    with transactional(factory) as s:
        return int(
            s.execute(
                select(PlanSectionModel.row_id).where(PlanSectionModel.letter == letter)
            ).scalar_one()
        )


def _revisions(factory: sessionmaker[Session]) -> int:
    with transactional(factory) as s:
        return int(
            s.execute(
                select(func.count(RevisionModel.row_id)).where(
                    RevisionModel.entity_kind == "plan_section"
                )
            ).scalar_one()
        )


def _events(factory: sessionmaker[Session], kind: str) -> int:
    with transactional(factory) as s:
        return int(
            s.execute(
                select(func.count(ActivityEventModel.row_id)).where(ActivityEventModel.kind == kind)
            ).scalar_one()
        )


def _all_revisions(factory: sessionmaker[Session]) -> int:
    with transactional(factory) as s:
        return int(s.execute(select(func.count(RevisionModel.row_id))).scalar_one())


def test_reason_required_on_new_tools(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    for name in _NEW_TOOLS:
        params = inspect.signature(tools[name]).parameters
        assert params["reason"].default is inspect.Parameter.empty
        assert params["author"].default == "mcp"

    before = _all_revisions(factory)
    with pytest.raises(ValueError, match="reason"):
        tools["plan_section_update"](
            project="alpha", plan_scope=_SCOPE, letter="A", title="X", reason=""
        )
    with pytest.raises(ValueError, match="reason"):
        tools["plan_section_move"](
            project="alpha", plan_scope=_SCOPE, letter="D", before="A", reason=""
        )
    with pytest.raises(ValueError, match="reason"):
        tools["plan_section_delete"](project="alpha", plan_scope=_SCOPE, letter="D", reason="")
    assert _all_revisions(factory) == before


def test_update_title_and_idempotent(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    revs = _revisions(factory)
    events = _events(factory, "plan.section_updated")

    out = tools["plan_section_update"](
        project="alpha", plan_scope=_SCOPE, letter="A", title="New title", reason="r"
    )

    assert set(out) == {"section_id", "plan_scope", "letter", "title", "slug", "position", "doc_id"}
    assert out["title"] == "New title"
    assert out["slug"] == "A-Alpha"
    assert out["plan_scope"] == "alpha-plan"
    assert _sections(factory)[0] == ("A", "New title", "A-Alpha", 0)
    assert _revisions(factory) == revs + 1
    assert _events(factory, "plan.section_updated") == events + 1

    tools["plan_section_update"](
        project="alpha", plan_scope=_SCOPE, letter="A", title="New title", reason="r"
    )
    assert _revisions(factory) == revs + 1
    assert _events(factory, "plan.section_updated") == events + 1


def test_update_warns_on_html_entity(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    out = tools["plan_section_update"](
        project="alpha", plan_scope=_SCOPE, letter="A", title="Q&amp;A", reason="r"
    )
    assert len(out["warnings"]) == 1
    assert "&amp;" in out["warnings"][0]
    assert _sections(factory)[0][1] == "Q&amp;A"

    clean = tools["plan_section_update"](
        project="alpha", plan_scope=_SCOPE, letter="B", title="Q&A", reason="r"
    )
    assert "warnings" not in clean


def test_move_before(tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]) -> None:
    out = tools["plan_section_move"](
        project="alpha", plan_scope=_SCOPE, letter="D", before="B", reason="r"
    )

    assert [(e["letter"], e["position"]) for e in out["order"]] == [
        ("A", 0),
        ("D", 1),
        ("B", 2),
        ("C", 3),
    ]
    assert [(r[0], r[3]) for r in _sections(factory)] == [
        ("A", 0),
        ("B", 2),
        ("C", 3),
        ("D", 1),
    ]


def test_move_argument_error(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        tools["plan_section_move"](project="alpha", plan_scope=_SCOPE, letter="D", reason="r")
    with pytest.raises(SectionNotFoundError):
        tools["plan_section_move"](
            project="alpha", plan_scope=_SCOPE, letter="D", before="Z", reason="r"
        )
    assert issubclass(SectionNotFoundError, LookupError)
    assert [(r[0], r[3]) for r in _sections(factory)] == [
        ("A", 0),
        ("B", 1),
        ("C", 2),
        ("D", 3),
    ]


def test_delete_requires_reassign_then_moves(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session], pids: dict[str, int]
) -> None:
    b_id = _section_id(factory, "B")
    c_id = _section_id(factory, "C")
    with transactional(factory) as session:
        plan_id = session.execute(
            select(PlanModel.row_id).where(PlanModel.scope == _SCOPE)
        ).scalar_one()
        for tid in ("TA-001", "TA-002"):
            task_service.create(
                session,
                project_id=pids["alpha"],
                plan_id=plan_id,
                section_id=b_id,
                task_id=tid,
                title=f"Implement: {tid}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
            )

    with pytest.raises(SectionHasTasksError):
        tools["plan_section_delete"](project="alpha", plan_scope=_SCOPE, letter="B", reason="r")
    assert [r[0] for r in _sections(factory)] == ["A", "B", "C", "D"]

    out = tools["plan_section_delete"](
        project="alpha", plan_scope=_SCOPE, letter="B", reassign_to="C", reason="r"
    )

    assert out["deleted"] is True
    assert out["moved_tasks"] == 2
    assert out["reassign_to"] == "C"
    assert [r[0] for r in _sections(factory)] == ["A", "C", "D"]
    with transactional(factory) as session:
        rows = session.execute(
            select(TaskModel.task_id, TaskModel.section_id).order_by(TaskModel.task_id)
        ).all()
    assert [(r[0], r[1]) for r in rows] == [("TA-001", c_id), ("TA-002", c_id)]


def test_foreign_project_plan_not_found(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    before = _sections(factory)
    revs = _all_revisions(factory)

    with pytest.raises(PlanNotFoundError):
        tools["plan_section_update"](
            project="beta", plan_scope=_SCOPE, letter="A", title="Hijack", reason="r"
        )

    assert _sections(factory) == before
    assert _all_revisions(factory) == revs
