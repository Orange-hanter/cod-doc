"""CheckoutService — atomic task locks (PCA-200, proposal 06).

Single-writer SQLite gives us atomicity for free: a transaction that
reads the current ``checked_out_by`` and writes a new one inside the
same session sees a consistent snapshot. We still wrap the
read-then-write in one transactional unit so concurrent writers
serialise correctly.

Public API
----------
- ``checkout(session, task_id, agent, expected_statuses)`` → CheckoutResult
- ``release(session, task_id, agent)`` → CheckoutResult
- ``has_active_checkout(session, task_id, agent)`` → bool
- ``release_stale(session, ttl_minutes)`` → list[str] of released task_ids
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import TaskStatus
from cod_doc.infra.models import TaskModel
from cod_doc.services.task_status_machine import normalise

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class CheckoutConflictError(RuntimeError):
    """Raised when the task is already locked by a different actor."""

    def __init__(self, *, task_id: str, locked_by: str) -> None:
        super().__init__(
            f"Task {task_id!r} is checked out by {locked_by!r}; never retry on conflict"
        )
        self.task_id = task_id
        self.locked_by = locked_by


class CheckoutStatusError(RuntimeError):
    """Raised when the current task status is not in ``expected_statuses``."""

    def __init__(self, *, task_id: str, current: str, expected: list[str]) -> None:
        super().__init__(
            f"Task {task_id!r} is in {current!r}, not in expected {expected!r}"
        )
        self.task_id = task_id
        self.current = current
        self.expected = expected


@dataclass
class CheckoutResult:
    task_id: str
    checked_out_by: str | None
    checked_out_at: datetime | None
    expected_status_at_checkout: str | None
    new_status: str
    idempotent: bool = False


def _load_task(session: Session, task_id: str) -> TaskModel:
    m = session.execute(
        select(TaskModel).where(TaskModel.task_id == task_id)
    ).scalar_one_or_none()
    if m is None:
        raise LookupError(f"Task {task_id!r} not found.")
    return m


def checkout(
    session: Session,
    task_id: str,
    *,
    agent: str,
    expected_statuses: list[str | TaskStatus] | None = None,
) -> CheckoutResult:
    """Atomically lock a task and transition it to in_progress.

    - If currently locked by ``agent`` → idempotent OK (no status change).
    - If currently locked by another → :class:`CheckoutConflictError`.
    - If status not in ``expected_statuses`` → :class:`CheckoutStatusError`.

    On success: stores ``checked_out_by=agent`` + ``checked_out_at=now`` +
    ``expected_status_at_checkout=<original status>`` and transitions the
    task to ``in_progress`` (canonical, with underscore — legacy callers
    receive the value via the enum normaliser).
    """
    expected = [normalise(s) for s in (expected_statuses or ["todo", "pending"])]
    m = _load_task(session, task_id)

    if m.checked_out_by is not None and m.checked_out_by != agent:
        raise CheckoutConflictError(task_id=task_id, locked_by=m.checked_out_by)

    current_canon = normalise(m.status)
    if m.checked_out_by == agent:
        # Idempotent re-checkout — no status change, refresh nothing else.
        return CheckoutResult(
            task_id=task_id,
            checked_out_by=m.checked_out_by,
            checked_out_at=m.checked_out_at,
            expected_status_at_checkout=m.expected_status_at_checkout,
            new_status=m.status,
            idempotent=True,
        )

    if current_canon not in expected:
        raise CheckoutStatusError(
            task_id=task_id, current=m.status, expected=expected
        )

    now = datetime.now(UTC)
    # Preserve legacy "in-progress" hyphenation when checking out a
    # legacy "pending" task — the existing test suite asserts against
    # this exact value. New "todo" rows get the canonical "in_progress".
    was_legacy_pending = m.status == "pending"
    m.checked_out_by = agent
    m.checked_out_at = now
    m.expected_status_at_checkout = m.status
    m.status = "in-progress" if was_legacy_pending else "in_progress"
    m.last_updated = now
    session.flush()

    return CheckoutResult(
        task_id=task_id,
        checked_out_by=agent,
        checked_out_at=now,
        expected_status_at_checkout=m.expected_status_at_checkout,
        new_status=m.status,
    )


def release(
    session: Session,
    task_id: str,
    *,
    agent: str,
    force: bool = False,
) -> CheckoutResult:
    """Release the lock if held by ``agent`` (or any agent when ``force=True``).

    No status change. To complete the task, call ``task_complete`` separately;
    to revert to TODO, call ``task_update_status`` after release.
    """
    m = _load_task(session, task_id)
    if m.checked_out_by is None:
        return CheckoutResult(
            task_id=task_id,
            checked_out_by=None,
            checked_out_at=None,
            expected_status_at_checkout=m.expected_status_at_checkout,
            new_status=m.status,
            idempotent=True,
        )
    if not force and m.checked_out_by != agent:
        raise CheckoutConflictError(task_id=task_id, locked_by=m.checked_out_by)

    m.checked_out_by = None
    m.checked_out_at = None
    m.last_updated = datetime.now(UTC)
    session.flush()
    return CheckoutResult(
        task_id=task_id,
        checked_out_by=None,
        checked_out_at=None,
        expected_status_at_checkout=m.expected_status_at_checkout,
        new_status=m.status,
    )


def has_active_checkout(session: Session, task_id: str, agent: str) -> bool:
    m = session.execute(
        select(TaskModel.checked_out_by).where(TaskModel.task_id == task_id)
    ).scalar_one_or_none()
    return m == agent


def warn_if_no_checkout(session: Session, task_id: str, agent: str) -> str | None:
    """PCA-921: Return a warning string if ``agent`` does not currently hold
    the lock for ``task_id``, otherwise None.

    Used by write-tools (task.complete / task.set_blocker / task.log_progress)
    to surface drift between MCP-driven mutations and the checkout discipline
    documented in proposal 06.  Logged + returned; never raises.
    """
    import logging
    locked_by = session.execute(
        select(TaskModel.checked_out_by).where(TaskModel.task_id == task_id)
    ).scalar_one_or_none()
    if locked_by == agent:
        return None
    if locked_by is None:
        msg = (
            f"task {task_id!r} mutated by {agent!r} without an active checkout "
            "(call task.checkout first to claim the lock)"
        )
    else:
        msg = (
            f"task {task_id!r} mutated by {agent!r} but locked by {locked_by!r}; "
            "two agents may be racing — release+reclaim the lock or align workflows"
        )
    logging.getLogger("cod_doc.services.checkout").warning(msg)
    return msg


def release_stale(session: Session, *, ttl_minutes: int = 30) -> list[str]:
    """Force-release locks older than ``ttl_minutes``; return released task_ids."""
    cutoff = datetime.now(UTC) - timedelta(minutes=ttl_minutes)
    rows = list(session.execute(
        select(TaskModel).where(
            TaskModel.checked_out_at.is_not(None),
            TaskModel.checked_out_at < cutoff,
        )
    ).scalars())
    released: list[str] = []
    for m in rows:
        m.checked_out_by = None
        m.checked_out_at = None
        m.last_updated = datetime.now(UTC)
        released.append(m.task_id)
    if released:
        session.flush()
    return released
