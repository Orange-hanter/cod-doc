"""STO-023: поиск по plan.scope / story_id / module_id скоупится проектом.

Миграция 0047 (STO-018) сделала эти ключи уникальными в проекте, а не во всей
БД. В hub-БД два проекта законно держат одинаковые ``cod-doc-bootstrap``,
``US-001`` и ``M1-auth``; поиск без ``project_id`` либо падает на
``MultipleResultsFound``, либо отдаёт строку чужого проекта.

Сид идёт через сервисы, а не ORM: ``create_plan`` и ``story_service.create``
сами проверяют уникальность и до STO-023 отвергали второй проект.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.domain.entities import Priority, StoryLinkKind, StoryRelation
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ModuleModel, PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.services import plan_service
from cod_doc.services import story_service as stories

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

SCOPE = "cod-doc-bootstrap"
STORY = "US-001"
MODULE = "M1-auth"
SLUGS = ("alpha", "beta")


def _seed(engine: Engine) -> tuple[sessionmaker[Session], dict[str, int]]:
    """Два проекта с одинаковыми scope, story_id и module_id."""
    factory = make_session_factory(engine)
    pids: dict[str, int] = {}
    with transactional(factory) as session:
        for slug in SLUGS:
            now = datetime.now(UTC)
            proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
            proj.created = now
            proj.updated = now
            session.add(proj)
            session.flush()
            pid = proj.row_id
            pids[slug] = pid
            plan_service.create_plan(
                session,
                project_id=pid,
                scope=SCOPE,
                principle=slug,
                author="human:test",
            )
            plan_service.create_section(
                session,
                project_id=pid,
                plan_scope=SCOPE,
                letter="A",
                title=f"Core {slug}",
                author="human:test",
            )
            stories.create(
                session,
                project_id=pid,
                story_id=STORY,
                persona=f"persona-{slug}",
                narrative="As X, I want Y, so Z.",
                priority=Priority.MEDIUM,
                author="human:test",
            )
            session.add(ModuleModel(project_id=pid, module_id=MODULE, name=slug, status="active"))
            session.flush()
    return factory, pids


@pytest.mark.parametrize("slug", SLUGS)
def test_plan_get_by_scope_returns_own_project_plan(engine_with_schema: Engine, slug: str) -> None:
    factory, pids = _seed(engine_with_schema)
    with transactional(factory) as session:
        plan = plan_service.get_by_scope(session, SCOPE, project_id=pids[slug])
        assert plan is not None
        assert plan.project_id == pids[slug]
        assert plan.principle == slug


@pytest.mark.parametrize("slug", SLUGS)
def test_story_lookups_return_own_project_story(engine_with_schema: Engine, slug: str) -> None:
    factory, pids = _seed(engine_with_schema)
    pid = pids[slug]
    with transactional(factory) as session:
        story = stories.get(session, STORY, project_id=pid)
        assert story is not None
        assert story.project_id == pid
        assert story.persona == f"persona-{slug}"

        # module_id тоже не уникален в БД: ссылка резолвит модуль своего проекта.
        stories.link(
            session,
            project_id=pid,
            story_id=STORY,
            to_kind=StoryLinkKind.MODULE,
            to_ref=MODULE,
            relation=StoryRelation.OWNED_BY,
            author="human:test",
        )
        links = stories.list_links(session, STORY, project_id=pid)
        assert [(lk.to_kind, lk.to_ref) for lk in links] == [(StoryLinkKind.MODULE, MODULE)]
        assert stories.coverage(session, STORY, project_id=pid).story_id == STORY

    # Связь записана только в историю этого проекта.
    other = next(s for s in SLUGS if s != slug)
    with transactional(factory) as session:
        assert stories.list_links(session, STORY, project_id=pids[other]) == []


def _mcp_tools(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> dict[str, Callable[..., Any]]:
    """task_create / story_get на свежем FastMCP; слаг резолвится по БД, как в бою."""
    from cod_doc.mcp.tools import story_tools, task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(story_tools, "session_factory", lambda project: (factory, None))
    mcp = FastMCP("test")
    task_tools.register(mcp)
    story_tools.register(mcp)
    return {name: mcp._tool_manager._tools[name].fn for name in ("task_create", "story_get")}


@pytest.mark.parametrize("slug", SLUGS)
def test_mcp_task_create_lands_in_own_project_plan(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine, slug: str
) -> None:
    factory, pids = _seed(engine_with_schema)
    tools = _mcp_tools(monkeypatch, factory)

    out = tools["task_create"](
        project=slug,
        plan_scope=SCOPE,
        section_letter="A",
        title=f"Task for {slug}",
        type="feature",
        priority="medium",
        task_id="BOOT-001",
    )
    assert out["plan_scope"] == SCOPE

    with transactional(factory) as session:
        row = session.execute(
            select(TaskModel.project_id, PlanModel.project_id, PlanSectionModel.title)
            .join(PlanModel, PlanModel.row_id == TaskModel.plan_id)
            .join(PlanSectionModel, PlanSectionModel.row_id == TaskModel.section_id)
            .where(TaskModel.title == f"Task for {slug}")
        ).one()
    assert tuple(row) == (pids[slug], pids[slug], f"Core {slug}")


@pytest.mark.parametrize("slug", SLUGS)
def test_mcp_story_get_returns_own_project_story(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine, slug: str
) -> None:
    factory, _ = _seed(engine_with_schema)
    tools = _mcp_tools(monkeypatch, factory)

    out = tools["story_get"](project=slug, story_id=STORY)
    assert out is not None
    assert out["persona"] == f"persona-{slug}"
