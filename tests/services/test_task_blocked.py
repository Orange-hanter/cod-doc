"""COD-055: structured blocked_reason on Task — set/clear/list."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import EntityKind, Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
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
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make(session: Session, p: int, pl: int, s: int, tid: str) -> None:
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
    )


def test_create_with_blocked_reason_persists_field(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="PR-001",
            title="Implement: foo",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
            blocked_reason="waiting on legal review",
        )
        t = task_service.get(session, "PR-001")
        assert t is not None
        assert t.blocked_reason == "waiting on legal review"


def test_create_default_blocked_reason_is_none(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "PR-001")
        t = task_service.get(session, "PR-001")
        assert t is not None
        assert t.blocked_reason is None


def test_set_blocker_stores_reason_and_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "PR-001")

        before = task_service.get(session, "PR-001")
        assert before is not None and before.row_id is not None

        task_service.set_blocker(
            session,
            task_id="PR-001",
            reason="waiting on stakeholder X",
            author="human:test",
        )

        after = task_service.get(session, "PR-001")
        assert after is not None
        assert after.blocked_reason == "waiting on stakeholder X"

        history = rev.list_for_entity(session, EntityKind.TASK, before.row_id)
        last = history[-1]
        assert "set_blocker" in last.diff
        assert "waiting on stakeholder X" in last.diff


def test_set_blocker_rejects_empty_reason(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "PR-001")

        with pytest.raises(ValueError, match="non-empty"):
            task_service.set_blocker(session, task_id="PR-001", reason="   ", author="human:test")


def test_clear_blocker_removes_reason(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "PR-001")

        task_service.set_blocker(session, task_id="PR-001", reason="x", author="human:test")
        task_service.clear_blocker(session, task_id="PR-001", author="human:test")

        t = task_service.get(session, "PR-001")
        assert t is not None
        assert t.blocked_reason is None


def test_clear_blocker_noop_when_already_clear(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "PR-001")
        before = task_service.get(session, "PR-001")
        assert before is not None and before.row_id is not None
        baseline = len(rev.list_for_entity(session, EntityKind.TASK, before.row_id))

        # No-op clear — should not write a revision
        task_service.clear_blocker(session, task_id="PR-001", author="human:test")

        after = len(rev.list_for_entity(session, EntityKind.TASK, before.row_id))
        assert after == baseline


def test_list_blocked_returns_tasks_with_blocker(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        for tid in ("PR-001", "PR-002", "PR-003"):
            _make(session, p, pl, s, tid)
        task_service.set_blocker(session, task_id="PR-001", reason="x", author="human:test")
        task_service.set_blocker(session, task_id="PR-003", reason="y", author="human:test")

        blocked = task_service.list_blocked(session, p)
        assert {t.task_id for t in blocked} == {"PR-001", "PR-003"}


def test_list_blocked_excludes_done_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Once a task is DONE its old blocker is historical noise."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "PR-001")
        task_service.set_blocker(session, task_id="PR-001", reason="x", author="human:test")
        task_service.update_status(
            session,
            task_id="PR-001",
            new_status=TaskStatus.DONE,
            author="human:test",
            force=True,
        )

        assert task_service.list_blocked(session, p) == []
