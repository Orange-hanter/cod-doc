"""COD-057: pagination, filters, and summary for task_service.list_for_project."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make(
    session: Session,
    proj: int,
    plan: int,
    sec: int,
    tid: str,
    *,
    priority: Priority = Priority.MEDIUM,
) -> None:
    task_service.create(
        session,
        project_id=proj,
        plan_id=plan,
        section_id=sec,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=priority,
        author="human:test",
    )


def test_limit_caps_returned_rows(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        for i in range(1, 11):
            _make(session, p, pl, s, f"PR-{i:03d}")

        rows = task_service.list_for_project(session, p, limit=3)
        assert len(rows) == 3
        assert [r.task_id for r in rows] == ["PR-001", "PR-002", "PR-003"]


def test_offset_skips_rows(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        for i in range(1, 6):
            _make(session, p, pl, s, f"PR-{i:03d}")

        rows = task_service.list_for_project(session, p, limit=2, offset=2)
        assert [r.task_id for r in rows] == ["PR-003", "PR-004"]


def test_priority_filter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "PR-001", priority=Priority.HIGH)
        _make(session, p, pl, s, "PR-002", priority=Priority.LOW)
        _make(session, p, pl, s, "PR-003", priority=Priority.HIGH)

        rows = task_service.list_for_project(session, p, priority=Priority.HIGH)
        assert {r.task_id for r in rows} == {"PR-001", "PR-003"}


def test_count_for_project_matches_filter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "PR-001", priority=Priority.HIGH)
        _make(session, p, pl, s, "PR-002", priority=Priority.LOW)
        _make(session, p, pl, s, "PR-003", priority=Priority.HIGH)

        assert task_service.count_for_project(session, p) == 3
        assert task_service.count_for_project(session, p, priority=Priority.HIGH) == 2
        assert task_service.count_for_project(session, p, status=TaskStatus.DONE) == 0


def test_summarize_aggregates_by_status_and_priority(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "PR-001", priority=Priority.HIGH)
        _make(session, p, pl, s, "PR-002", priority=Priority.LOW)
        _make(session, p, pl, s, "PR-003", priority=Priority.HIGH)

        # Move one to in-progress
        task_service.update_status(
            session,
            task_id="PR-002",
            new_status=TaskStatus.IN_PROGRESS,
            author="human:test",
        )

        summary = task_service.summarize_for_project(session, p)
        assert summary["total"] == 3
        assert summary["by_status"]["pending"] == 2
        assert summary["by_status"]["in-progress"] == 1
        assert summary["by_priority"]["high"] == 2
        assert summary["by_priority"]["low"] == 1


def test_legacy_signature_still_works(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Backwards compat: status-only call without limit returns all tasks."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        for i in range(1, 4):
            _make(session, p, pl, s, f"PR-{i:03d}")

        rows = task_service.list_for_project(session, p, status=TaskStatus.PENDING)
        assert len(rows) == 3
