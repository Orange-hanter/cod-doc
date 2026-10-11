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

from cod_doc.domain.entities import Priority, StoryLinkKind, StoryRelation, TaskStatus, TaskType
from cod_doc.infra.db import transactional
from cod_doc.infra.models import TaskModel
from cod_doc.infra.repositories import PlanSectionRepository, TaskRepository
from cod_doc.services import (
    agent_service,
    checkout_service,
    heartbeat_service,
    plan_service,
    task_service,
)
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


# --------------------------------------------------------------------------- #
# STO-030: write-пути задач скоупятся проектом                                 #
# --------------------------------------------------------------------------- #


def _task_row(session: Session, pid: int) -> tuple[str, str | None, str | None]:
    task = task_service.get(session, TASK, project_id=pid)
    assert task is not None
    model = session.get(TaskModel, task.row_id)
    assert model is not None
    return model.status, model.checked_out_by, model.description


def _other(slug: str) -> str:
    return next(s for s in SLUGS if s != slug)


@pytest.mark.parametrize("slug", SLUGS)
def test_checkout_locks_only_own_project_task(engine_with_schema: Engine, slug: str) -> None:
    """Глобальный поиск в ``_load_task`` ставил замок на одноимённую задачу соседа."""
    factory, pids = _seed_same_task_id(engine_with_schema)
    with transactional(factory) as session:
        checkout_service.checkout(session, TASK, project_id=pids[slug], agent="agent:a")
    with transactional(factory) as session:
        assert _task_row(session, pids[slug])[:2] == ("in_progress", "agent:a")
        assert _task_row(session, pids[_other(slug)])[:2] == ("todo", None)


@pytest.mark.parametrize("slug", SLUGS)
def test_release_unlocks_only_own_project_task(engine_with_schema: Engine, slug: str) -> None:
    factory, pids = _seed_same_task_id(engine_with_schema)
    with transactional(factory) as session:
        for s in SLUGS:
            checkout_service.checkout(session, TASK, project_id=pids[s], agent="agent:a")
        checkout_service.release(session, TASK, project_id=pids[slug], agent="agent:a")
    with transactional(factory) as session:
        assert _task_row(session, pids[slug])[1] is None
        assert _task_row(session, pids[_other(slug)])[1] == "agent:a"


@pytest.mark.parametrize("slug", SLUGS)
def test_complete_closes_only_own_project_task(engine_with_schema: Engine, slug: str) -> None:
    factory, pids = _seed_same_task_id(engine_with_schema)
    with transactional(factory) as session:
        task_service.complete(session, project_id=pids[slug], task_id=TASK, author="human:test")
    with transactional(factory) as session:
        assert _task_row(session, pids[slug])[0] == "done"
        assert _task_row(session, pids[_other(slug)])[0] == "todo"


@pytest.mark.parametrize("slug", SLUGS)
def test_update_status_changes_only_own_project_task(engine_with_schema: Engine, slug: str) -> None:
    factory, pids = _seed_same_task_id(engine_with_schema)
    with transactional(factory) as session:
        task_service.update_status(
            session,
            project_id=pids[slug],
            task_id=TASK,
            new_status=TaskStatus.CANCELLED,
            author="human:test",
        )
    with transactional(factory) as session:
        assert _task_row(session, pids[slug])[0] == "cancelled"
        assert _task_row(session, pids[_other(slug)])[0] == "todo"


@pytest.mark.parametrize("slug", SLUGS)
def test_update_description_changes_only_own_project_task(
    engine_with_schema: Engine, slug: str
) -> None:
    factory, pids = _seed_same_task_id(engine_with_schema)
    with transactional(factory) as session:
        task_service.update_description(
            session,
            project_id=pids[slug],
            task_id=TASK,
            new_description=f"desc of {slug}",
            author="human:test",
        )
    with transactional(factory) as session:
        assert _task_row(session, pids[slug])[2] == f"desc of {slug}"
        assert _task_row(session, pids[_other(slug)])[2] is None


def test_task_lookup_by_id_has_no_global_variant() -> None:
    """STO-030: глобального ``get_by_task_id`` нет — забыть скоуп нечем."""
    assert not hasattr(TaskRepository, "get_by_task_id")


@pytest.mark.parametrize("bad", [None, ""])
def test_get_in_project_rejects_empty_task_id(engine_with_schema: Engine, bad: object) -> None:
    factory, pids = _seed(engine_with_schema)
    with transactional(factory) as session, pytest.raises((TypeError, ValueError)):
        TaskRepository(session).get_in_project(bad, project_id=pids["alpha"])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# STO-030: чтения по task_id — heartbeat и цепочки зависимостей                #
# --------------------------------------------------------------------------- #

_EXTRA = {"alpha": "ADO-010", "beta": "ADO-020"}


def _seed_chain(engine: Engine, *, forward: bool) -> tuple[sessionmaker[Session], dict[str, int]]:
    """В каждом проекте ADO-001 связан ребром со своей задачей из ``_EXTRA``.

    ``forward=True`` — своя задача блокирует ADO-001 (prerequisite);
    иначе ADO-001 блокирует свою задачу (dependent).
    """
    factory, pids = _seed_same_task_id(engine)
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
                task_id=_EXTRA[slug],
                title=f"Extra of {slug}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
            )
            task_id, blocker = (TASK, _EXTRA[slug]) if forward else (_EXTRA[slug], TASK)
            task_service.add_dependency(
                session,
                project_id=pid,
                task_id=task_id,
                blocker_task_id=blocker,
                note="test edge",
                author="human:test",
            )
    return factory, pids


@pytest.mark.parametrize("slug", SLUGS)
def test_forward_chain_walks_own_project_only(engine_with_schema: Engine, slug: str) -> None:
    """Старт CTE по одному task_id собирал цепочки обоих проектов сразу."""
    factory, pids = _seed_chain(engine_with_schema, forward=True)
    with transactional(factory) as session:
        chain = plan_service.forward_chain(session, TASK, project_id=pids[slug])
    assert [e.task_id for e in chain] == [_EXTRA[slug]]


@pytest.mark.parametrize("slug", SLUGS)
def test_reverse_chain_walks_own_project_only(engine_with_schema: Engine, slug: str) -> None:
    factory, pids = _seed_chain(engine_with_schema, forward=False)
    with transactional(factory) as session:
        chain = plan_service.reverse_chain(session, TASK, project_id=pids[slug])
    assert [e.task_id for e in chain] == [_EXTRA[slug]]


@pytest.mark.parametrize("slug", SLUGS)
def test_heartbeat_context_returns_own_project_task(engine_with_schema: Engine, slug: str) -> None:
    factory, pids = _seed_same_task_id(engine_with_schema)
    with transactional(factory) as session:
        out = heartbeat_service.heartbeat_context(session, project_id=pids[slug], task_id=TASK)
    assert out["task"]["id"] == TASK
    assert out["task"]["title"] == f"Task of {slug}"
