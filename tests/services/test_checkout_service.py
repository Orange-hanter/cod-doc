"""PCA-200: CheckoutService — atomic task locks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.services import checkout_service as checkout
from cod_doc.services import task_service as tasks
from cod_doc.services.checkout_service import (
    CheckoutConflictError,
    CheckoutStatusError,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_task(session: Session, task_id: str = "CO-001", status: str = "pending") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="co", title="CO", root_path="/tmp/co", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="co-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Sec", slug="A-Sec", position=0,
    )
    session.add(sec)
    session.flush()
    t = tasks.create(
        session,
        project_id=proj.row_id,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id=task_id,
        title="t",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="x",
    )
    if status != "pending":
        # Force any starting status (e.g. for testing checkout from "todo").
        m = session.query(TaskModel).filter(TaskModel.task_id == task_id).one()
        m.status = status
        session.flush()
    return t.row_id


def test_checkout_legacy_pending_promotes_to_in_progress_hyphen(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        result = checkout.checkout(session, "CO-001", agent="orchestrator-run-X")
        assert result.checked_out_by == "orchestrator-run-X"
        assert result.expected_status_at_checkout == "pending"
        # Legacy hyphen preserved when source was "pending".
        assert result.new_status == "in-progress"
        assert result.idempotent is False


def test_checkout_canonical_todo_promotes_to_underscore(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session, status="todo")
        result = checkout.checkout(session, "CO-001", agent="run-X")
        assert result.expected_status_at_checkout == "todo"
        assert result.new_status == "in_progress"


def test_checkout_idempotent_same_agent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        first = checkout.checkout(session, "CO-001", agent="agent-A")
        second = checkout.checkout(session, "CO-001", agent="agent-A")
        assert second.idempotent is True
        assert second.checked_out_by == first.checked_out_by
        # SQLite strips tzinfo on round-trip; compare naive timestamps.
        assert second.checked_out_at.replace(tzinfo=None) == first.checked_out_at.replace(tzinfo=None)


def test_checkout_conflict_different_agent_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        checkout.checkout(session, "CO-001", agent="agent-A")
        with pytest.raises(CheckoutConflictError, match="agent-A"):
            checkout.checkout(session, "CO-001", agent="agent-B")


def test_checkout_status_mismatch_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session, status="done")
        with pytest.raises(CheckoutStatusError, match="done"):
            checkout.checkout(session, "CO-001", agent="A",
                              expected_statuses=["todo", "pending"])


def test_release_clears_lock(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        checkout.checkout(session, "CO-001", agent="A")
        result = checkout.release(session, "CO-001", agent="A")
        assert result.checked_out_by is None
        assert result.idempotent is False


def test_release_idempotent_when_no_lock(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        result = checkout.release(session, "CO-001", agent="anyone")
        assert result.idempotent is True


def test_release_different_agent_raises_without_force(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        checkout.checkout(session, "CO-001", agent="A")
        with pytest.raises(CheckoutConflictError):
            checkout.release(session, "CO-001", agent="B")


def test_release_force_bypasses_owner_check(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        checkout.checkout(session, "CO-001", agent="A")
        result = checkout.release(session, "CO-001", agent="admin", force=True)
        assert result.checked_out_by is None


def test_has_active_checkout_distinguishes_owner(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        checkout.checkout(session, "CO-001", agent="A")
        assert checkout.has_active_checkout(session, "CO-001", agent="A") is True
        assert checkout.has_active_checkout(session, "CO-001", agent="B") is False


def test_release_stale_releases_old_locks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        checkout.checkout(session, "CO-001", agent="A")
        # Backdate the lock past the TTL.
        m = session.query(TaskModel).filter(TaskModel.task_id == "CO-001").one()
        m.checked_out_at = datetime.now(UTC) - timedelta(hours=2)
        session.flush()
        released = checkout.release_stale(session, ttl_minutes=30)
        assert released == ["CO-001"]


def test_release_stale_skips_fresh_locks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        checkout.checkout(session, "CO-001", agent="A")
        released = checkout.release_stale(session, ttl_minutes=30)
        assert released == []


def test_checkout_unknown_task_raises_lookup(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(LookupError):
        checkout.checkout(session, "NO-SUCH", agent="A")
