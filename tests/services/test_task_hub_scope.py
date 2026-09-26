"""ADO-200 (RFC 26 §3.2, T9): поиск задачи скоупится проектом.

Ограничение целостности — ``UNIQUE (project_id, task_id)``. В общей hub-БД
два проекта с одинаковым ``task_id`` роняли ``scalar_one_or_none()`` через
``MultipleResultsFound`` в ``_require_task``, ``remove_dependency`` и
поиске блокера ``create(blocked_by=...)``. Эталоны — литералы и прямые
SELECT по таблице ``dependency``; ожидание через ``_require_task`` /
``remove_dependency`` / ``task_to_dict`` не строится.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services import task_service
from cod_doc.services.task_service import TaskNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


def _seed_project(session: Session, slug: str) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope=f"{slug}-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make(
    session: Session,
    ids: tuple[int, int, int],
    tid: str,
    *,
    blocked_by: list[str] | None = None,
) -> None:
    p, pl, s = ids
    task_service.create(
        session,
        project_id=p,
        plan_id=pl,
        section_id=s,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
        blocked_by=blocked_by,
    )


def _row_id(session: Session, project_id: int, task_id: str) -> int:
    return session.execute(
        select(TaskModel.row_id).where(
            TaskModel.project_id == project_id, TaskModel.task_id == task_id
        )
    ).scalar_one()


def _edge_count(session: Session, from_row: int, to_row: int) -> int:
    return session.execute(
        select(func.count())
        .select_from(DependencyModel)
        .where(
            DependencyModel.from_task_id == from_row,
            DependencyModel.to_task_id == to_row,
            DependencyModel.kind == "blocks",
        )
    ).scalar_one()


def _seed_hub_with_edges(
    session: Session,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Два проекта, в каждом TA-001 и TA-002 с ребром TA-002 → TA-001."""
    alpha = _seed_project(session, "alpha")
    beta = _seed_project(session, "beta")
    for ids in (alpha, beta):
        _make(session, ids, "TA-001")
        _make(session, ids, "TA-002", blocked_by=["TA-001"])
    return alpha, beta


def test_require_task_project_id_has_no_default() -> None:
    for fn in (task_service._require_task, task_service.remove_dependency):
        param = inspect.signature(fn).parameters["project_id"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


def test_blocked_by_resolves_within_project(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        beta = _seed_project(session, "beta")
        _make(session, alpha, "TA-001")
        _make(session, beta, "TA-001")

        _make(session, beta, "TA-002", blocked_by=["TA-001"])
        session.flush()

        beta_task = _row_id(session, beta[0], "TA-002")
        beta_blocker = _row_id(session, beta[0], "TA-001")
        alpha_blocker = _row_id(session, alpha[0], "TA-001")

        edges = session.execute(
            select(DependencyModel.to_task_id).where(DependencyModel.from_task_id == beta_task)
        ).all()
        assert len(edges) == 1
        assert edges[0][0] == beta_blocker
        assert edges[0][0] != alpha_blocker


def test_blocked_by_unknown_in_project_raises(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        beta = _seed_project(session, "beta")
        _make(session, alpha, "ONLYA-001")

        with pytest.raises(ValueError, match="ONLYA-001"):
            _make(session, beta, "TA-002", blocked_by=["ONLYA-001"])


def test_remove_dependency_scoped_to_project(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha, beta = _seed_hub_with_edges(session)
        session.flush()

        task_service.remove_dependency(
            session,
            project_id=beta[0],
            task_id="TA-002",
            blocker_task_id="TA-001",
            author="human:test",
        )
        session.flush()

        alpha_edges = _edge_count(
            session, _row_id(session, alpha[0], "TA-002"), _row_id(session, alpha[0], "TA-001")
        )
        beta_edges = _edge_count(
            session, _row_id(session, beta[0], "TA-002"), _row_id(session, beta[0], "TA-001")
        )
        assert alpha_edges == 1
        assert beta_edges == 0


def test_remove_dependency_other_project_task_not_found(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        beta = _seed_project(session, "beta")
        _make(session, alpha, "ONLYA-001")
        _make(session, alpha, "ONLYA-002", blocked_by=["ONLYA-001"])

        with pytest.raises(TaskNotFoundError):
            task_service.remove_dependency(
                session,
                project_id=beta[0],
                task_id="ONLYA-002",
                blocker_task_id="ONLYA-001",
                author="human:test",
            )


def test_mcp_task_remove_dependency_hub(
    monkeypatch: pytest.MonkeyPatch, engine_with_schema: Engine
) -> None:
    from cod_doc.mcp.tools import task_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha, beta = _seed_hub_with_edges(session)
    pids = {"alpha": alpha[0], "beta": beta[0]}

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: pids[project])
    mcp = FastMCP("test")
    task_tools.register(mcp)
    tool: Any = mcp._tool_manager._tools["task_remove_dependency"].fn

    tool(project="beta", task_id="TA-002", blocker_id="TA-001")

    with transactional(factory) as session:
        alpha_edges = _edge_count(
            session, _row_id(session, alpha[0], "TA-002"), _row_id(session, alpha[0], "TA-001")
        )
        beta_edges = _edge_count(
            session, _row_id(session, beta[0], "TA-002"), _row_id(session, beta[0], "TA-001")
        )
    assert alpha_edges == 1
    assert beta_edges == 0
