"""STO-023: поиск по plan.scope / story_id / module_id скоупится проектом.

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

from cod_doc.domain.entities import StoryLinkKind, StoryRelation
from cod_doc.infra.db import transactional
from cod_doc.services import plan_service
from cod_doc.services import story_service as stories
from tests._project_scoped_seed import MODULE, SCOPE, SLUGS, STORY
from tests._project_scoped_seed import seed_two_projects as _seed

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


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


@pytest.mark.parametrize("bad", [None, "1", True])
def test_scoped_repo_lookups_reject_non_int_project_id(
    engine_with_schema: Engine, bad: object
) -> None:
    """``project_id=None`` дал бы ``WHERE project_id IS NULL`` и тихое «не найдено»."""
    from cod_doc.infra.repositories import PlanRepository, UserStoryRepository

    factory, _ = _seed(engine_with_schema)
    with transactional(factory) as session:
        with pytest.raises(TypeError, match="project_id must be int"):
            PlanRepository(session).get_by_scope(SCOPE, project_id=bad)
        with pytest.raises(TypeError, match="project_id must be int"):
            UserStoryRepository(session).get_by_story_id(STORY, project_id=bad)
