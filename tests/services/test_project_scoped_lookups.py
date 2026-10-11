"""STO-023/029: поиск по plan.scope / story_id / module_id / task_id скоупится проектом.

Миграция 0047 (STO-018) сделала эти ключи уникальными в проекте, а не во всей
БД. В hub-БД два проекта законно держат одинаковые ``cod-doc-bootstrap``,
``US-001`` и ``M1-auth``; поиск без ``project_id`` либо падает на
``MultipleResultsFound``, либо отдаёт строку чужого проекта.

Сид — ``tests/_project_scoped_seed.py`` (планы и истории через сервисы, модуль
через ORM); MCP-сторона тех же поисков — ``tests/test_project_scoped_tools_mcp.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import Priority, StoryLinkKind, StoryRelation, TaskType
from cod_doc.infra.db import transactional
from cod_doc.infra.repositories import PlanSectionRepository
from cod_doc.services import agent_service, plan_service, task_service
from cod_doc.services import story_service as stories
from tests._project_scoped_seed import MODULE, SCOPE, SLUGS, STORY
from tests._project_scoped_seed import seed_two_projects as _seed

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker


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


TASK = "ADO-001"


def _seed_same_task_id(engine: Engine) -> tuple[sessionmaker[Session], dict[str, int]]:
    """Поверх общего сида — задача ``ADO-001`` в каждом проекте (STO-029)."""
    factory, pids = _seed(engine)
    with transactional(factory) as session:
        for slug, pid in pids.items():
            plan = plan_service.get_by_scope(session, SCOPE, project_id=pid)
            assert plan is not None and plan.row_id is not None
            (section,) = PlanSectionRepository(session).list_for_plan(plan.row_id)
            assert section.row_id is not None
            task_service.create(
                session,
                project_id=pid,
                plan_id=plan.row_id,
                section_id=section.row_id,
                task_id=TASK,
                title=f"Task of {slug}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
            )
    return factory, pids


@pytest.mark.parametrize("slug", SLUGS)
def test_task_get_returns_own_project_task(engine_with_schema: Engine, slug: str) -> None:
    """task_id уникален в проекте: глобальный поиск падал на MultipleResultsFound."""
    factory, pids = _seed_same_task_id(engine_with_schema)
    with transactional(factory) as session:
        task = task_service.get(session, TASK, project_id=pids[slug])
        assert task is not None
        assert task.project_id == pids[slug]
        assert task.title == f"Task of {slug}"


@pytest.mark.parametrize("slug", SLUGS)
def test_agent_get_related_task_returns_own_project_task(
    engine_with_schema: Engine, slug: str
) -> None:
    """Ветка ``related_task`` не отдаёт карточку задачи чужого проекта."""
    factory, pids = _seed_same_task_id(engine_with_schema)
    with transactional(factory) as session:
        out = agent_service.get(
            session, project_id=pids[slug], task_id=TASK, what="related_task", ref=TASK
        )
    assert out["found"] is True
    assert out["payload"]["title"] == f"Task of {slug}"
