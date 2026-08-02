"""COD-012 / RFL-074: PlanService — audit."""

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


def test_audit_clean_plan_has_no_issues(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        a = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        b = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()

        report = plans.audit(session, plan_id)
        assert report.cycles == []
        assert report.done_with_unfinished_blocks == []
        assert report.issues_total == 0


def test_audit_detects_cycle(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        a = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        b = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        c = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-003")
        # A -> B -> C -> A (cycle)
        session.add(DependencyModel(from_task_id=a.row_id, to_task_id=b.row_id, kind="blocks"))
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=c.row_id, kind="blocks"))
        session.add(DependencyModel(from_task_id=c.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()

        report = plans.audit(session, plan_id)
        assert len(report.cycles) >= 1
        cycle_ids = {tid for cyc in report.cycles for tid in cyc}
        assert cycle_ids == {"PLN-001", "PLN-002", "PLN-003"}
        assert report.issues_total >= 1


def test_audit_ignores_non_blocks_in_cycle_check(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """relates / duplicates edges don't form cycles."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        a = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        b = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        session.add(DependencyModel(from_task_id=a.row_id, to_task_id=b.row_id, kind="relates"))
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="relates"))
        session.flush()

        report = plans.audit(session, plan_id)
        assert report.cycles == []


def test_audit_flags_done_with_unfinished_blocks(engine_with_schema) -> None:
    """Drift: a task somehow marked done while a blocking dep is still open."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        a = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        b = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()
        tasks.update_status(
            session, task_id="PLN-002", new_status=TaskStatus.DONE, author="drift", force=True
        )

        report = plans.audit(session, plan_id)
        assert "PLN-002" in report.done_with_unfinished_blocks
        assert report.issues_total >= 1
