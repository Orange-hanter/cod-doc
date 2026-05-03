"""COD-012 / RFL-074: PlanService — export."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import plan_service as plans
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_plan_with_sections(
    session: Session, sections: list[tuple[str, str, str]] | None = None
) -> tuple[int, int, dict[str, int]]:
    sections = sections or [("A", "Data Core", "A-Data-Core")]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(
        project_id=proj.row_id,
        scope="p-plan",
        principle="test-first",
        created=now,
        last_updated=now,
    )
    session.add(plan)
    session.flush()
    sec_ids: dict[str, int] = {}
    for i, (letter, title, slug) in enumerate(sections):
        sec = PlanSectionModel(
            plan_id=plan.row_id, letter=letter, title=title, slug=slug, position=i
        )
        session.add(sec)
        session.flush()
        sec_ids[letter] = sec.row_id
    return proj.row_id, plan.row_id, sec_ids


def _seed_task(
    session: Session,
    *,
    proj_id: int,
    plan_id: int,
    section_id: int,
    task_id: str,
    status: TaskStatus = TaskStatus.PENDING,
    priority: Priority = Priority.MEDIUM,
):  # type: ignore[no-untyped-def]
    t = tasks.create(
        session,
        project_id=proj_id,
        plan_id=plan_id,
        section_id=section_id,
        task_id=task_id,
        title=f"Task {task_id}",
        type=TaskType.FEATURE,
        priority=priority,
        author="human:test",
    )
    if status is not TaskStatus.PENDING:
        tasks.update_status(session, task_id=task_id, new_status=status, author="human:test")
    return t


def test_export_progress_overview_has_section_rows(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(
            session, [("A", "Data Core", "A-Data-Core"), ("B", "Services", "B-Services")]
        )
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        tasks.complete(session, task_id="PLN-001", author="x")
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["B"], task_id="PLN-010")

        out = plans.export(session, plan_id)
        po = out["progress_overview"]
        assert "Progress Overview" in po
        assert "A: Data Core" in po
        assert "B: Services" in po
        assert "TOTAL" in po
        assert "done" in po
        assert "pending" in po


def test_export_next_batch_lists_ready_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-001",
            priority=Priority.CRITICAL,
        )
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-002",
            priority=Priority.LOW,
        )

        out = plans.export(session, plan_id)
        nb = out["next_batch"]
        assert "Next Batch" in nb
        assert nb.index("PLN-001") < nb.index("PLN-002")


def test_export_dependency_graph_is_mermaid(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        a = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        b = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()

        out = plans.export(session, plan_id)
        dg = out["dependency_graph"]
        assert "```mermaid" in dg
        assert "graph TD" in dg
        assert "PLN_001" in dg
        assert "PLN_002" in dg
        assert "PLN_001 --> PLN_002" in dg


def test_export_empty_plan_renders_placeholders(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _, plan_id, _ = _seed_plan_with_sections(session)
        out = plans.export(session, plan_id)
        assert "Progress Overview" in out["progress_overview"]
        assert "Next Batch" in out["next_batch"]
        assert "graph TD" in out["dependency_graph"]
