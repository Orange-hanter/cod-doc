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
        plan_id=plan.row_id,
        letter="A",
        title="Sec",
        slug="A-Sec",
        position=0,
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
    # Статус выставляется ВСЕГДА и мимо сервисов: после ADO-156 `create()`
    # пишет канонический `todo`, и легаси-строку, какая лежит в ещё не
    # мигрированной БД, иначе не получить.
    m = session.query(TaskModel).filter(TaskModel.task_id == task_id).one()
    m.status = status
    session.flush()
    return t.row_id


def test_checkout_of_a_legacy_pending_row_lands_canonical(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Легаси-строка на входе принимается, но чекаут приземляется в канон (ADO-156).

    До этого написание результата зависело от написания источника, и одно и
    то же состояние лежало в базе двумя строками.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_task(session)
        result = checkout.checkout(session, "CO-001", agent="orchestrator-run-X")
        assert result.checked_out_by == "orchestrator-run-X"
        # Снимок «что было до чекаута» — история, её не канонизируем.
        assert result.expected_status_at_checkout == "pending"
        assert result.new_status == "in_progress"
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
        assert second.checked_out_at.replace(tzinfo=None) == first.checked_out_at.replace(
            tzinfo=None
        )


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
            checkout.checkout(session, "CO-001", agent="A", expected_statuses=["todo", "pending"])


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


def _seed_locked_task(session: Session, *, slug: str, task_id: str) -> int:
    """Отдельный проект со своей задачей под замком двухчасовой давности.

    Возвращает ``project_id`` — по нему тесты и режут чистку.
    """
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope=f"{slug}-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Sec", slug="A-Sec", position=0)
    session.add(sec)
    session.flush()
    tasks.create(
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
    checkout.checkout(session, task_id, agent="agent-A")
    m = session.query(TaskModel).filter(TaskModel.task_id == task_id).one()
    m.checked_out_at = now - timedelta(hours=2)
    session.flush()
    return int(proj.row_id)


def test_release_stale_scoped_to_project_spares_the_neighbour(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-192: в hub-БД чистка одного проекта не смеет трогать чужие замки."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha_id = _seed_locked_task(session, slug="alpha", task_id="AL-001")
        _seed_locked_task(session, slug="beta", task_id="BE-001")

        assert checkout.release_stale(session, ttl_minutes=30, project_id=alpha_id) == ["AL-001"]

        neighbour = session.query(TaskModel).filter(TaskModel.task_id == "BE-001").one()
        assert neighbour.checked_out_by == "agent-A"


def test_release_stale_without_project_id_stays_db_wide(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Умолчание не изменилось: функция остаётся общей чисткой всей БД."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_locked_task(session, slug="alpha", task_id="AL-001")
        _seed_locked_task(session, slug="beta", task_id="BE-001")

        assert sorted(checkout.release_stale(session, ttl_minutes=30)) == ["AL-001", "BE-001"]


def test_checkout_unknown_task_raises_lookup(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(LookupError):
        checkout.checkout(session, "NO-SUCH", agent="A")
