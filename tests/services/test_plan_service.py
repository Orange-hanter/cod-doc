"""COD-012: PlanService — recalc / ready / audit / export."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

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
    """Seed project + plan + named sections.

    sections: list of (letter, title, slug). Defaults to a single A section.
    Returns (project_id, plan_id, {letter: section_id}).
    """
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
    """Create a task and (if needed) flip its status without going through `complete()`."""
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


# ============================================================================ #
# recalc                                                                       #
# ============================================================================ #


def test_recalc_empty_plan_is_empty(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _, plan_id, _ = _seed_plan_with_sections(session)
        progress = plans.recalc(session, plan_id)

        assert progress.plan_id == plan_id
        assert progress.total == 0
        assert progress.done == 0
        assert progress.status is plans.DerivedStatus.EMPTY
        assert len(progress.sections) == 1
        assert progress.sections[0].status is plans.DerivedStatus.EMPTY


def test_recalc_section_pending_and_in_progress(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(
            session, [("A", "Core", "A-Core"), ("B", "Svc", "B-Svc")]
        )
        # A: 2 pending tasks → status=pending
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        # B: 1 in-progress + 1 pending → status=in-progress
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["B"],
            task_id="PLN-010",
            status=TaskStatus.IN_PROGRESS,
        )
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["B"], task_id="PLN-011")

        progress = plans.recalc(session, plan_id)
        by_letter = {s.letter: s for s in progress.sections}

        assert by_letter["A"].status is plans.DerivedStatus.PENDING
        assert by_letter["A"].total == 2 and by_letter["A"].done == 0
        assert by_letter["B"].status is plans.DerivedStatus.IN_PROGRESS
        assert by_letter["B"].in_progress == 1
        assert progress.status is plans.DerivedStatus.IN_PROGRESS  # rolls up
        assert progress.total == 4
        assert progress.remaining == 4


def test_recalc_section_done_when_all_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        tasks.complete(session, task_id="PLN-001", author="x")
        tasks.complete(session, task_id="PLN-002", author="x")

        progress = plans.recalc(session, plan_id)
        assert progress.status is plans.DerivedStatus.DONE
        assert progress.sections[0].status is plans.DerivedStatus.DONE
        assert progress.done == 2 and progress.remaining == 0


def test_recalc_partial_done_is_in_progress(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Section with some tasks done but others pending = in-progress (no in-progress tasks needed)."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        tasks.complete(session, task_id="PLN-001", author="x")

        progress = plans.recalc(session, plan_id)
        assert progress.sections[0].status is plans.DerivedStatus.IN_PROGRESS
        assert progress.status is plans.DerivedStatus.IN_PROGRESS


def test_recalc_unknown_plan_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(plans.PlanNotFoundError):
        plans.recalc(session, 9999)


# ============================================================================ #
# ready                                                                        #
# ============================================================================ #


def test_ready_returns_pending_tasks_with_done_deps(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        a = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")
        b = _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        # B blocked by A
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()

        # Both initially pending; only A is ready (no incoming blockers from itself).
        ready = plans.ready(session, plan_id)
        assert {t.task_id for t in ready} == {"PLN-001"}

        # After A done, B becomes ready.
        tasks.complete(session, task_id="PLN-001", author="x")
        ready = plans.ready(session, plan_id)
        assert {t.task_id for t in ready} == {"PLN-002"}


def test_ready_excludes_in_progress_and_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-001",
            status=TaskStatus.IN_PROGRESS,
        )
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-002")
        tasks.complete(session, task_id="PLN-002", author="x")
        # A pending task to verify it shows up.
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-003")

        ready = plans.ready(session, plan_id)
        assert {t.task_id for t in ready} == {"PLN-003"}


def test_ready_scoped_to_plan(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ready() must not leak tasks from other plans."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        # Plan 1
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(session, proj_id=p, plan_id=plan_id, section_id=secs["A"], task_id="PLN-001")

        # Plan 2 in same project
        now = datetime.now(UTC)
        plan2 = PlanModel(project_id=p, scope="other-plan", created=now, last_updated=now)
        session.add(plan2)
        session.flush()
        sec2 = PlanSectionModel(plan_id=plan2.row_id, letter="A", title="X", slug="A-X", position=0)
        session.add(sec2)
        session.flush()
        _seed_task(
            session, proj_id=p, plan_id=plan2.row_id, section_id=sec2.row_id, task_id="QQ-001"
        )

        ready = plans.ready(session, plan_id)
        assert {t.task_id for t in ready} == {"PLN-001"}


def test_ready_priority_order(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ready() returns tasks priority-sorted: critical > high > medium > low, then by task_id."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-001",
            priority=Priority.LOW,
        )
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-002",
            priority=Priority.CRITICAL,
        )
        _seed_task(
            session,
            proj_id=p,
            plan_id=plan_id,
            section_id=secs["A"],
            task_id="PLN-003",
            priority=Priority.MEDIUM,
        )

        ready = plans.ready(session, plan_id)
        assert [t.task_id for t in ready] == ["PLN-002", "PLN-003", "PLN-001"]


def test_ready_respects_limit(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p, plan_id, secs = _seed_plan_with_sections(session)
        for n in range(1, 6):
            _seed_task(
                session,
                proj_id=p,
                plan_id=plan_id,
                section_id=secs["A"],
                task_id=f"PLN-00{n}",
            )
        ready = plans.ready(session, plan_id, limit=3)
        assert len(ready) == 3


# ============================================================================ #
# audit                                                                        #
# ============================================================================ #


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
        # B blocked by A; complete B by directly mutating status (bypassing complete()).
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()
        tasks.update_status(session, task_id="PLN-002", new_status=TaskStatus.DONE, author="drift")

        report = plans.audit(session, plan_id)
        assert "PLN-002" in report.done_with_unfinished_blocks
        assert report.issues_total >= 1


# ============================================================================ #
# export                                                                       #
# ============================================================================ #


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
        # Status column should reflect derived state.
        assert "done" in po  # section A is done
        assert "pending" in po  # section B is pending


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
        # Critical-priority task should appear first in the rendered list.
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
        # Mermaid node IDs must be ASCII-safe (no hyphens) — task IDs become PLN_001 etc.
        assert "PLN_001" in dg
        assert "PLN_002" in dg
        # Edge: blocks-dep B → A means A must precede B; arrow goes from blocker to blocked.
        assert "PLN_001 --> PLN_002" in dg


def test_export_empty_plan_renders_placeholders(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _, plan_id, _ = _seed_plan_with_sections(session)
        out = plans.export(session, plan_id)
        # Sections present but no task rows; next_batch empty.
        assert "Progress Overview" in out["progress_overview"]
        assert "Next Batch" in out["next_batch"]
        assert "graph TD" in out["dependency_graph"]
