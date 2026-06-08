"""PCA-941: plan_sections_list MCP-тул возвращает секции + task counts.

Закрывает discoverability-gap: task_create требует ``section_letter``,
а до этого тула узнать валидные буквы можно было только через
``plan_export`` и парсинг markdown.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.mcp.tools import plan_tools
from cod_doc.services import task_service


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def test_plan_sections_list_returns_letters_titles_and_counts(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)

    # Seed: project + plan + 3 sections + 4 tasks (2 in A, 1 in B, 1 done in A).
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()

        plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
        session.add(plan)
        session.flush()

        sec_a = PlanSectionModel(
            plan_id=plan.row_id, letter="A", title="Alpha", slug="A-alpha", position=0
        )
        sec_b = PlanSectionModel(
            plan_id=plan.row_id, letter="B", title="Beta", slug="B-beta", position=1
        )
        sec_c = PlanSectionModel(
            plan_id=plan.row_id, letter="C", title="Gamma", slug="C-gamma", position=2
        )
        session.add_all([sec_a, sec_b, sec_c])
        session.flush()

        # 3 tasks in A (1 done), 1 in B, 0 in C.
        for i, (sec_id, _status) in enumerate(
            [
                (sec_a.row_id, TaskStatus.PENDING),
                (sec_a.row_id, TaskStatus.PENDING),
                (sec_a.row_id, TaskStatus.PENDING),
                (sec_b.row_id, TaskStatus.PENDING),
            ]
        ):
            task = task_service.create(
                session,
                project_id=proj.row_id,
                plan_id=plan.row_id,
                section_id=sec_id,
                task_id=f"PLN-{i + 1:03d}",
                title=f"task {i}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="t",
            )
            if i == 0:
                # Mark one of A's tasks as done.
                from cod_doc.infra.models import TaskModel

                m = session.get(TaskModel, task.row_id)
                assert m is not None
                m.status = TaskStatus.DONE
                session.flush()

    from cod_doc.mcp.tools import plan_tools as pt

    monkeypatch.setattr(pt, "session_factory", lambda project: (factory, None))

    mcp = FastMCP("test")
    plan_tools.register(mcp)
    plan_sections_list = _get_tool(mcp, "plan_sections_list")

    result = plan_sections_list(project="p", plan_scope="p-plan")

    assert isinstance(result, list)
    assert [r["letter"] for r in result] == ["A", "B", "C"]
    by_letter = {r["letter"]: r for r in result}
    assert by_letter["A"]["task_count"] == 3
    assert by_letter["A"]["done_count"] == 1
    assert by_letter["B"]["task_count"] == 1
    assert by_letter["B"]["done_count"] == 0
    assert by_letter["C"]["task_count"] == 0
    assert by_letter["C"]["done_count"] == 0
    # Each row carries title + slug + position for discoverability.
    for r in result:
        assert "title" in r and "slug" in r and "position" in r
        assert "section_id" in r


def test_plan_sections_list_unknown_plan_raises(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()

    from cod_doc.mcp.tools import plan_tools as pt

    monkeypatch.setattr(pt, "session_factory", lambda project: (factory, None))

    mcp = FastMCP("test")
    plan_tools.register(mcp)
    plan_sections_list = _get_tool(mcp, "plan_sections_list")

    import pytest

    with pytest.raises(ValueError, match="not found"):
        plan_sections_list(project="p", plan_scope="nonexistent")
