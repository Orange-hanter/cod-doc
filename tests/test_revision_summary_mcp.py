"""ADO-228 (RFC 27 F9, находка N7): MCP ``revision_summary`` — агрегат ревизий.

Один вызов вместо выгрузки ленты ``revision_list`` и группировки на клиенте —
как ``activity_summary(group_by)`` для событий. Эталоны — литералы; строить
ожидание вызовом ``revision_service.summarize``/``list_for_project``
запрещено спекой. Исключение — тест сходимости: сумма ``n`` против длины
``revision_list`` в режиме ленты, где сравнение двух тулов и есть предмет
проверки.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import Plan, PlanSection
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DocumentModel,
    RevisionModel,
    SectionModel,
    TaskModel,
)
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
    now = _dt(9, 1, 10)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="pr", title="pr", root_path="/tmp/pr", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()

    plan = PlanRepository(session).add(
        Plan(project_id=proj.row_id, scope="plan-x", principle="test-first")
    )
    plan.created = now
    plan.last_updated = now
    session.flush()
    plan_sec = PlanSectionRepository(session).add(
        PlanSection(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    )
    session.flush()

    task = TaskModel(
        project_id=proj.row_id,
        task_id="ADO-228-T1",
        plan_id=plan.row_id,
        section_id=plan_sec.row_id,
        title="ADO-228 seed task",
        status="todo",
        type="feature",
        priority="medium",
        created=now,
        last_updated=now,
    )
    doc = DocumentModel(
        project_id=proj.row_id,
        doc_key="guide",
        path="docs/guide.md",
        type="guide",
        status="active",
        title="Guide",
        created=now,
        last_updated=now,
    )
    session.add_all([task, doc])
    session.flush()
    section = SectionModel(
        document_id=doc.row_id,
        anchor="intro",
        heading="Intro",
        level=2,
        position=0,
        body="intro body",
        content_hash="abc",
    )
    session.add(section)
    session.flush()

    revisions = [
        RevisionModel(
            revision_id="rev-01",
            project_id=proj.row_id,
            entity_kind="task",
            entity_id=task.row_id,
            parent_revision_id=None,
            author="alice",
            at=_dt(9, 25, 10),
            diff='{"op":"create"}',
            reason="create task",
        ),
        RevisionModel(
            revision_id="rev-02",
            project_id=proj.row_id,
            entity_kind="task",
            entity_id=task.row_id,
            parent_revision_id="rev-01",
            author="bob",
            at=_dt(9, 26, 9),
            diff='{"op":"status"}',
            reason="status change",
        ),
        RevisionModel(
            revision_id="rev-03",
            project_id=proj.row_id,
            entity_kind="task",
            entity_id=task.row_id,
            parent_revision_id="rev-02",
            author="alice",
            at=_dt(9, 26, 11),
            diff='{"op":"title"}',
            reason="title edit",
        ),
        RevisionModel(
            revision_id="rev-04",
            project_id=proj.row_id,
            entity_kind="section",
            entity_id=section.row_id,
            parent_revision_id=None,
            author="alice",
            at=_dt(9, 26, 8),
            diff="--- section+++ section@@",
            reason="patch section",
        ),
        RevisionModel(
            revision_id="rev-05",
            project_id=proj.row_id,
            entity_kind="section",
            entity_id=section.row_id,
            parent_revision_id=None,
            author="carol",
            at=_dt(9, 25, 12),
            diff="--- /dev/null+++ section@@",
            reason="add section",
        ),
        RevisionModel(
            revision_id="rev-06",
            project_id=proj.row_id,
            entity_kind="document",
            entity_id=doc.row_id,
            parent_revision_id=None,
            author="bob",
            at=_dt(9, 26, 7, 30),
            diff='{"op":"rename"}',
            reason="rename doc",
        ),
    ]
    session.add_all(revisions)
    session.flush()
    return proj.row_id


def _tools(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> dict[str, Any]:
    from cod_doc.mcp.tools import revision_tools

    factory = make_session_factory(engine)
    with transactional(factory) as s:
        proj_id = _seed(s)
    monkeypatch.setattr(revision_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(revision_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    revision_tools.register(mcp)
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


def test_revision_summary_today_by_entity_kind_one_call(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision_summary = _tools(monkeypatch, engine)["revision_summary"]

    result = revision_summary(project="pr", since="2026-09-26")

    assert result == [
        {"entity_kind": "document", "n": 1},
        {"entity_kind": "section", "n": 1},
        {"entity_kind": "task", "n": 2},
    ]


def test_revision_summary_sum_matches_revision_list(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = _tools(monkeypatch, engine)
    revision_summary = tools["revision_summary"]
    revision_list = tools["revision_list"]

    today = revision_summary(project="pr", since="2026-09-26")
    feed_today = revision_list(project="pr", since="2026-09-26", limit=1000)
    assert sum(row["n"] for row in today) == len(feed_today) == 4

    by_day_author = revision_summary(project="pr", since="2026-09-25", group_by=["day", "author"])
    feed_all = revision_list(project="pr", since="2026-09-25", limit=1000)
    assert by_day_author == [
        {"day": "2026-09-25", "author": "alice", "n": 1},
        {"day": "2026-09-25", "author": "carol", "n": 1},
        {"day": "2026-09-26", "author": "alice", "n": 2},
        {"day": "2026-09-26", "author": "bob", "n": 2},
    ]
    assert sum(row["n"] for row in by_day_author) == len(feed_all) == 6


def test_revision_summary_invalid_group_by_is_error(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision_summary = _tools(monkeypatch, engine)["revision_summary"]

    with pytest.raises(ValueError) as excinfo:
        revision_summary(project="pr", since="2026-09-26", group_by=["week"])
    for key in ("day", "entity_kind", "author"):
        assert key in str(excinfo.value)

    with pytest.raises(ValueError):
        revision_summary(project="pr", since="yesterday")


def test_revision_summary_group_by_author(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    revision_summary = _tools(monkeypatch, engine)["revision_summary"]

    result = revision_summary(project="pr", since="2026-09-26", group_by=["author"])

    assert result == [
        {"author": "alice", "n": 2},
        {"author": "bob", "n": 2},
    ]
    for row in result:
        assert set(row) == {"author", "n"}
