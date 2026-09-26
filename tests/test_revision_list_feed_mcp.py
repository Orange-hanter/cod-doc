"""AFT-008 (RFC 27 F9): MCP ``revision_list`` — проектная лента ревизий.

``kind``/``ref`` опциональны: оба заданы — история сущности как раньше
(регресс критерия 2), ни одного — лента проекта newest-first с фильтрами
``since``/``author``/``entity_kind`` (критерий 1: «ревизии за сегодня по
видам сущностей» одним вызовом, группировка на стороне клиента). Эталоны —
литералы; строить ожидание вызовом
``revision_service.list_for_project``/``list_for_entity`` запрещено спекой.
"""

from __future__ import annotations

from collections import Counter
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
        task_id="AFT-008-T1",
        plan_id=plan.row_id,
        section_id=plan_sec.row_id,
        title="AFT-008 seed task",
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
        # task history: one yesterday, two today
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
        # sections: one today, one yesterday
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
        # document rename today
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


def _revision_list(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> Any:
    from cod_doc.mcp.tools import revision_tools

    factory = make_session_factory(engine)
    with transactional(factory) as s:
        proj_id = _seed(s)
    monkeypatch.setattr(revision_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(revision_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    revision_tools.register(mcp)
    return mcp._tool_manager._tools["revision_list"].fn


def test_entity_mode_unchanged(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    revision_list = _revision_list(monkeypatch, engine)

    result = revision_list(project="pr", kind="task", ref="AFT-008-T1", limit=2)

    assert [r["revision_id"] for r in result] == ["rev-02", "rev-03"]
    for row in result:
        assert set(row) == {"revision_id", "author", "at", "reason", "parent_revision_id", "diff"}
    assert [r["reason"] for r in result] == ["status change", "title edit"]


def test_feed_today_one_call(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    revision_list = _revision_list(monkeypatch, engine)

    result = revision_list(project="pr", since="2026-09-26")

    assert [r["revision_id"] for r in result] == ["rev-03", "rev-02", "rev-04", "rev-06"]
    for row in result:
        assert "entity_kind" in row
        assert "diff" not in row
    assert Counter(r["entity_kind"] for r in result) == {
        "task": 2,
        "section": 1,
        "document": 1,
    }


def test_feed_author_and_entity_kind(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    revision_list = _revision_list(monkeypatch, engine)

    by_author = revision_list(project="pr", author="alice")
    assert [r["revision_id"] for r in by_author] == ["rev-03", "rev-04", "rev-01"]

    by_kind = revision_list(project="pr", entity_kind="section")
    assert [r["revision_id"] for r in by_kind] == ["rev-04", "rev-05"]


def test_kind_without_ref_is_error(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    revision_list = _revision_list(monkeypatch, engine)

    with pytest.raises(ValueError, match="both or neither"):
        revision_list(project="pr", kind="task")


def test_feed_filters_with_entity_is_error(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    revision_list = _revision_list(monkeypatch, engine)

    with pytest.raises(ValueError, match="cannot be combined"):
        revision_list(project="pr", kind="task", ref="AFT-008-T1", since="2026-09-26")


def test_feed_bad_entity_kind_is_error(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    revision_list = _revision_list(monkeypatch, engine)

    with pytest.raises(ValueError) as excinfo:
        revision_list(project="pr", entity_kind="bogus")
    assert "task" in str(excinfo.value)
    assert "document" in str(excinfo.value)
