"""AFT-022: один ``task_id`` в двух проектах общей hub-БД.

Нумерация задач — на проект, поэтому в hub-БД ``ADO-001`` обычно есть у
нескольких проектов сразу. Поиск по голому ``task_id`` тогда либо падает
``MultipleResultsFound``, либо попадает в чужой проект. Здесь — пути, которые
ревью PR #192 нашло непокрытыми: цепочки зависимостей страницы задачи, смена
статуса, создание задачи по scope чужого плана, legacy REST, откат ревизии,
удаление легаси-путей, пакетное чтение задач.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.api import legacy_tasks
from cod_doc.domain.entities import EntityKind, Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    AffectedFileModel,
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
)
from cod_doc.services import agent_service, checkout_service
from cod_doc.services import plan_service as plans
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_TID = "ADO-001"


class _Proj(NamedTuple):
    project: int
    plan: int
    section: int
    task_row: int


def _project_with_task(session: Session, slug: str) -> _Proj:
    """Проект ``slug`` с планом ``<slug>-plan`` и задачей ``ADO-001``."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/repo/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope=f"{slug}-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="S", slug="A-S", position=0)
    session.add(sec)
    session.flush()
    task = tasks.create(
        session,
        project_id=proj.row_id,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id=_TID,
        title=f"Task of {slug}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    assert task.row_id is not None
    return _Proj(proj.row_id, plan.row_id, sec.row_id, task.row_id)


def _tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _task_tools(monkeypatch: pytest.MonkeyPatch, factory: Any) -> FastMCP:
    from cod_doc.mcp.tools import task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    mcp = FastMCP("test")
    task_tools.register(mcp)
    return mcp


def test_dependency_chains_resolve_the_task_inside_the_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Страница задачи: цепочки по ``task_id`` в скоупе проекта, не ``MultipleResultsFound``."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        _project_with_task(session, "b")
        blocker = tasks.create(
            session,
            project_id=a.project,
            plan_id=a.plan,
            section_id=a.section,
            task_id="ADO-002",
            title="Blocker",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )
        tasks.add_dependency(
            session,
            project_id=a.project,
            task_id=_TID,
            blocker_task_id=blocker.task_id,
            note="blocker first",
            author="human:test",
        )

        forward = plans.forward_chain(session, _TID, project_id=a.project)
        reverse = plans.reverse_chain(session, "ADO-002", project_id=a.project)

        assert [e.task_id for e in forward] == ["ADO-002"]
        assert [e.task_id for e in reverse] == [_TID]


def test_update_status_and_mcp_status_tool_stay_in_the_project(
    engine_with_schema, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        b = _project_with_task(session, "b")
        tasks.update_status(
            session,
            task_id=_TID,
            new_status=TaskStatus.BLOCKED,
            author="human:test",
            project_id=b.project,
        )
        assert tasks.get(session, _TID, project_id=b.project).status is TaskStatus.BLOCKED  # type: ignore[union-attr]
        assert tasks.get(session, _TID, project_id=a.project).status is TaskStatus.TODO  # type: ignore[union-attr]

    task_update_status = _tool(_task_tools(monkeypatch, factory), "task_update_status")
    task_update_status(project="a", task_id=_TID, new_status="cancelled")
    with transactional(factory) as session:
        assert tasks.get(session, _TID, project_id=a.project).status is TaskStatus.CANCELLED  # type: ignore[union-attr]
        assert tasks.get(session, _TID, project_id=b.project).status is TaskStatus.BLOCKED  # type: ignore[union-attr]


def test_mcp_task_create_does_not_see_the_plan_of_another_project(
    engine_with_schema, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """Scope чужого плана — понятное «Plan not found», а не ошибка про row_id."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _project_with_task(session, "a")
        _project_with_task(session, "b")

    task_create = _tool(_task_tools(monkeypatch, factory), "task_create")
    with pytest.raises(ValueError, match=r"^Plan 'a-plan' not found\.$"):
        task_create(
            project="b",
            plan_scope="a-plan",
            section_letter="A",
            title="Leak attempt",
            type="feature",
            priority="low",
        )


def test_legacy_rest_plan_is_per_project_when_the_scope_is_taken(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Второй проект hub-БД получает собственный legacy-план, а не чужой."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        b = _project_with_task(session, "b")

        plan_a, section_a = legacy_tasks.ensure_legacy_plan(session, a.project)
        plan_b, section_b = legacy_tasks.ensure_legacy_plan(session, b.project)

        assert plan_a != plan_b
        assert session.get(PlanModel, plan_a).scope == legacy_tasks.LEGACY_PLAN_SCOPE  # type: ignore[union-attr]
        assert session.get(PlanModel, plan_b).scope == f"legacy-rest-api-p{b.project}"  # type: ignore[union-attr]
        assert legacy_tasks.ensure_legacy_plan(session, b.project) == (plan_b, section_b)
        created = tasks.create(
            session,
            project_id=b.project,
            plan_id=plan_b,
            section_id=section_b,
            title="Via legacy REST",
            type=TaskType.FEATURE,
            priority=Priority.LOW,
            author="api-legacy",
        )
        assert created.project_id == b.project
        assert section_a != section_b


def test_revert_of_a_revision_whose_task_is_elsewhere_says_why(
    engine_with_schema, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """«Ревизия есть, сущность не здесь» — не «Revision not found»."""
    from cod_doc.mcp.tools import revision_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        b = _project_with_task(session, "b")
        tasks.update_affects_files(
            session, task_id=_TID, paths=["src/a.py"], author="human:test", project_id=a.project
        )
        target = rev.list_for_entity(session, EntityKind.TASK, a.task_row)[-1]
        model = session.execute(
            select(RevisionModel).where(RevisionModel.revision_id == target.revision_id)
        ).scalar_one()
        model.project_id = b.project  # рассинхрон легаси-импорта
        session.flush()

    monkeypatch.setattr(revision_tools, "session_factory", lambda project: (factory, None))
    mcp = FastMCP("test")
    revision_tools.register(mcp)
    revert = _tool(mcp, "revision_revert")

    with pytest.raises(ValueError, match="cannot be reverted: task #"):
        revert(project="b", revision_id=target.revision_id)
    with pytest.raises(ValueError, match=r"^Revision '01HQX5Z9F0K8R0000000000000' not found"):
        revert(project="b", revision_id="01HQX5Z9F0K8R0000000000000")


def test_remove_mode_clears_legacy_paths_written_before_the_rule(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        for legacy in ("src/a​.py", " src/padded.py ", "src/keep.py"):
            session.add(AffectedFileModel(task_id=a.task_row, path=legacy, kind="source"))
        session.flush()

        change = tasks.update_affects_files(
            session,
            task_id=_TID,
            paths=["src/a​.py", " src/padded.py "],
            mode="remove",
            author="human:test",
            project_id=a.project,
        )

        assert change.new == ["src/keep.py"]


def test_get_many_reads_only_the_project_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        _project_with_task(session, "b")

        got = tasks.get_many(session, [_TID, "NOPE-1"], project_id=a.project)

        assert list(got) == [_TID]
        assert got[_TID].project_id == a.project
        assert tasks.get_many(session, [], project_id=a.project) == {}


def test_task_mutations_act_on_the_task_of_the_given_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """complete / blocker / progress / checkout / release с ``project_id`` трогают
    задачу своего проекта, а не падают ``MultipleResultsFound``."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        b = _project_with_task(session, "b")

        checkout_service.checkout(session, _TID, agent="agent:x", project_id=b.project)
        tasks.log_progress(
            session, task_id=_TID, message="half way", author="agent:x", project_id=b.project
        )
        checkout_service.release(session, _TID, agent="agent:x", project_id=b.project)
        tasks.set_blocker(
            session, task_id=_TID, reason="waiting", author="human:t", project_id=a.project
        )
        tasks.clear_blocker(session, task_id=_TID, author="human:t", project_id=a.project)
        tasks.complete(session, task_id=_TID, author="human:t", project_id=b.project)

        task_a = tasks.get(session, _TID, project_id=a.project)
        task_b = tasks.get(session, _TID, project_id=b.project)
        assert task_a is not None and task_b is not None
        assert task_a.status is TaskStatus.TODO
        assert task_a.blocked_reason is None
        assert task_b.status is TaskStatus.DONE


def test_mcp_checkout_stays_in_the_project(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import checkout_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        b = _project_with_task(session, "b")

    monkeypatch.setattr(checkout_tools, "session_factory", lambda project: (factory, None))
    mcp = FastMCP("test")
    checkout_tools.register(mcp)
    _tool(mcp, "task_checkout")(project="b", task_id=_TID, agent="agent:x")

    with transactional(factory) as session:
        assert tasks.get(session, _TID, project_id=b.project).status is TaskStatus.IN_PROGRESS_NEW  # type: ignore[union-attr]
        assert tasks.get(session, _TID, project_id=a.project).status is TaskStatus.TODO  # type: ignore[union-attr]


def test_dependency_chain_does_not_walk_into_another_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Легаси-ребро в задачу чужого проекта не выводит её в цепочку."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        b = _project_with_task(session, "b")
        session.add(DependencyModel(from_task_id=a.task_row, to_task_id=b.task_row, kind="blocks"))
        session.flush()

        assert plans.forward_chain(session, _TID, project_id=a.project) == []
        assert plans.reverse_chain(session, _TID, project_id=b.project) == []


def test_agent_release_and_blocker_report_stay_in_the_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """agent_release возвращает в todo через сервис (с ревизией) и в скоупе
    проекта; agent_report(blocker) блокирует задачу своего проекта."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        b = _project_with_task(session, "b")
        checkout_service.checkout(session, _TID, agent="agent:x", project_id=b.project)
        before = len(rev.list_for_entity(session, EntityKind.TASK, b.task_row))

        out = agent_service.release(
            session, project_id=b.project, task_id=_TID, agent_id="agent:x", reason="handover"
        )

        assert out["ok"] is True
        assert tasks.get(session, _TID, project_id=b.project).status is TaskStatus.TODO  # type: ignore[union-attr]
        assert len(rev.list_for_entity(session, EntityKind.TASK, b.task_row)) == before + 1

        agent_service.report(
            session, project_id=a.project, task_id=_TID, kind="blocker", message="waiting"
        )
        assert tasks.get(session, _TID, project_id=a.project).status is TaskStatus.BLOCKED  # type: ignore[union-attr]
        assert tasks.get(session, _TID, project_id=b.project).status is TaskStatus.TODO  # type: ignore[union-attr]


def test_legacy_plan_creation_race_recovers(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Параллельный запрос успел создать план между чтением и вставкой: уникальный
    scope даёт IntegrityError, повторный разбор находит готовый план."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project_with_task(session, "a")
        first = legacy_tasks.ensure_legacy_plan(session, a.project)

        real_get = legacy_tasks.plan_service.get_by_scope
        stale = {"left": 1}

        def stale_once(session: Session, scope: str, **kwargs: Any) -> Any:
            if stale["left"]:
                stale["left"] -= 1
                return None  # «не видим» план, созданный параллельным запросом
            return real_get(session, scope, **kwargs)

        monkeypatch.setattr(legacy_tasks.plan_service, "get_by_scope", stale_once)
        assert legacy_tasks.ensure_legacy_plan(session, a.project) == first


def test_mcp_task_get_reads_the_task_of_its_project(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _project_with_task(session, "a")
        _project_with_task(session, "b")

    task_get = _tool(_task_tools(monkeypatch, factory), "task_get")
    assert task_get(project="b", task_id=_TID)["title"] == "Task of b"
    assert task_get(project="a", task_id=_TID)["title"] == "Task of a"
