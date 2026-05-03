"""COD-056: stale-detection + log_progress."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import EntityKind, Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.services import revision_service as rev
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
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
    )
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make_in_progress(
    session: Session,
    proj: int,
    plan: int,
    sec: int,
    tid: str,
    *,
    last_updated_offset_hours: float = 0.0,
) -> int:
    task_service.create(
        session,
        project_id=proj,
        plan_id=plan,
        section_id=sec,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    task_service.update_status(
        session,
        task_id=tid,
        new_status=TaskStatus.IN_PROGRESS,
        author="human:test",
    )
    if last_updated_offset_hours:
        # Backdate last_updated so we can test stale-cutoff.
        from sqlalchemy import update

        backdated = datetime.now(UTC) - timedelta(hours=last_updated_offset_hours)
        session.execute(
            update(TaskModel)
            .where(TaskModel.task_id == tid)
            .values(last_updated=backdated)
        )
        session.flush()
    return 0


def test_list_stale_returns_tasks_older_than_threshold(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_in_progress(session, p, pl, s, "PR-001", last_updated_offset_hours=48)
        _make_in_progress(session, p, pl, s, "PR-002", last_updated_offset_hours=2)

        stale = task_service.list_stale_in_progress(session, p, threshold_hours=24)

        assert [t.task_id for t in stale] == ["PR-001"]


def test_list_stale_ignores_pending_and_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Stale only flags IN_PROGRESS tasks — PENDING/DONE are out of scope."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        # PENDING for 48h — should not appear
        task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="PR-001",
            title="Implement: x",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )
        # IN_PROGRESS for 48h — should appear
        _make_in_progress(session, p, pl, s, "PR-002", last_updated_offset_hours=48)

        from sqlalchemy import update

        backdated = datetime.now(UTC) - timedelta(hours=48)
        session.execute(
            update(TaskModel).where(TaskModel.task_id == "PR-001").values(last_updated=backdated)
        )
        session.flush()

        stale = task_service.list_stale_in_progress(session, p, threshold_hours=24)
        assert [t.task_id for t in stale] == ["PR-002"]


def test_list_stale_threshold_zero_returns_all_in_progress(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """threshold_hours=0 means 'anything older than now' → all in-progress except just-created."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_in_progress(session, p, pl, s, "PR-001", last_updated_offset_hours=1)

        stale = task_service.list_stale_in_progress(session, p, threshold_hours=0)
        assert len(stale) == 1


def test_log_progress_touches_last_updated(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_in_progress(session, p, pl, s, "PR-001", last_updated_offset_hours=48)

        # Sanity: task is stale before progress note
        before = task_service.list_stale_in_progress(session, p, threshold_hours=24)
        assert [t.task_id for t in before] == ["PR-001"]

        task_service.log_progress(
            session, task_id="PR-001", message="reading spec", author="agent:x"
        )

        # After progress note: no longer stale
        after = task_service.list_stale_in_progress(session, p, threshold_hours=24)
        assert after == []


def test_log_progress_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_in_progress(session, p, pl, s, "PR-001")
        t = task_service.get(session, "PR-001")
        assert t is not None and t.row_id is not None

        task_service.log_progress(
            session, task_id="PR-001", message="step 2 of 5", author="agent:x"
        )

        history = rev.list_for_entity(session, EntityKind.TASK, t.row_id)
        last = history[-1]
        assert "progress" in last.diff
        assert "step 2 of 5" in last.diff


def test_log_progress_rejects_empty_message(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_in_progress(session, p, pl, s, "PR-001")

        with pytest.raises(ValueError, match="non-empty"):
            task_service.log_progress(
                session, task_id="PR-001", message="   ", author="agent:x"
            )
