"""CheckoutService — atomic task locks (PCA-200, proposal 06).

Single-writer SQLite gives us atomicity for free: a transaction that
reads the current ``checked_out_by`` and writes a new one inside the
same session sees a consistent snapshot. We still wrap the
read-then-write in one transactional unit so concurrent writers
serialise correctly.

Public API
----------
- ``checkout(session, task_id, project_id, agent, expected_statuses)`` → CheckoutResult
- ``release(session, task_id, project_id, agent)`` → CheckoutResult
- ``has_active_checkout(session, task_id, agent, project_id)`` → bool
- ``release_stale(session, ttl_minutes, project_id)`` → list[str] of released task_ids

Remote (RFC 29 §3.6, Q-011; CLO-031). With a remote configured for the
project, ``checkout`` first takes the lease ``leases/checkout/<task_uid>`` on
the remote by CAS and only then writes the DB; a failed write (rollback)
drops the lease again. Lease held → ``SYNC-108`` (``CheckoutLeaseHeldError``,
a ``CheckoutConflictError``); remote unreachable → ``SYNC-109``
(``CheckoutLeaseUnavailableError``); ``offline=True`` takes a provisional
lock that the next push/pull turns into a lease. ``release``,
``task_service.complete`` and ``release_stale`` drop the lease after commit.
Mechanics: ``sync_service/checkout_lease.py``. Without a remote — ADO-039 as
before, no network at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.hlc import self_replica
from cod_doc.infra.models import TaskModel
from cod_doc.infra.repositories.base import require_int_project_id
from cod_doc.services import activity_service
from cod_doc.services.sync_service import checkout_lease

# SVC-004: классы живут в листовом `task_errors`; здесь реэкспорт под прежними именами.
from cod_doc.services.task_errors import CheckoutConflictError as CheckoutConflictError
from cod_doc.services.task_errors import CheckoutStatusError as CheckoutStatusError
from cod_doc.services.task_errors import TaskNotFoundError
from cod_doc.services.task_status_machine import normalise

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import TaskStatus


@dataclass
class CheckoutResult:
    task_id: str
    checked_out_by: str | None
    checked_out_at: datetime | None
    expected_status_at_checkout: str | None
    new_status: str
    idempotent: bool = False
    #: CLO-031: ``"leased"`` — аренда на remote, ``"offline"`` — провизорный
    #: замок, ``None`` — remote не настроен (ADO-039).
    lease: str | None = None


def _load_task(session: Session, task_id: str, *, project_id: int) -> TaskModel:
    # STO-030: task_id уникален в проекте, не во всей БД (миграция 0047) —
    # без скоупа замок в hub-БД ставился бы на одноимённую задачу соседа.
    require_int_project_id(project_id)
    m = session.execute(
        select(TaskModel).where(TaskModel.project_id == project_id, TaskModel.task_id == task_id)
    ).scalar_one_or_none()
    if m is None:
        raise TaskNotFoundError(f"Task {task_id!r} not found.")
    return m


def _locked_by(session: Session, task_id: str, *, project_id: int) -> str | None:
    require_int_project_id(project_id)
    return session.execute(
        select(TaskModel.checked_out_by).where(
            TaskModel.project_id == project_id, TaskModel.task_id == task_id
        )
    ).scalar_one_or_none()


def checkout(
    session: Session,
    task_id: str,
    *,
    project_id: int,
    agent: str,
    expected_statuses: list[str | TaskStatus] | None = None,
    offline: bool = False,
) -> CheckoutResult:
    """Atomically lock a task and transition it to in_progress.

    - If currently locked by ``agent`` → idempotent OK (no status change).
    - If currently locked by another → :class:`CheckoutConflictError`.
    - If status not in ``expected_statuses`` → :class:`CheckoutStatusError`.

    On success: stores ``checked_out_by=agent`` + ``checked_out_at=now`` +
    ``expected_status_at_checkout=<original status>`` and transitions the
    task to ``in_progress`` (canonical, with underscore — legacy callers
    receive the value via the enum normaliser).

    With a remote (CLO-031) the lease is taken after the local checks and
    before the first DB write — the caller must not have written in this
    session yet, or the network call would sit inside its write transaction:

    - lease held by another agent or replica → ``SYNC-108``
      (``CheckoutLeaseHeldError``, subclass of ``CheckoutConflictError``);
    - remote unreachable → ``SYNC-109`` (``CheckoutLeaseUnavailableError``);
    - ``offline=True`` → provisional lock, the next push/pull leases it.

    Idempotent re-checkout by the same agent checks the lease too: a lock
    synced from another machine under the same agent name is still SYNC-108,
    and a provisional lock is leased now unless ``offline`` is passed again.
    """

    expected = [normalise(s) for s in (expected_statuses or ["todo", "pending"])]
    m = _load_task(session, task_id, project_id=project_id)

    if m.checked_out_by is not None and m.checked_out_by != agent:
        raise CheckoutConflictError(task_id=task_id, locked_by=m.checked_out_by)

    idempotent = m.checked_out_by == agent
    if not idempotent and normalise(m.status) not in expected:
        raise CheckoutStatusError(task_id=task_id, current=m.status, expected=expected)

    now = datetime.now(UTC)
    # Строка без uid (до миграции 0049) на remote не адресуется — ADO-039.
    target = checkout_lease.remote_for(session, project_id) if m.uid else None
    lease: str | None = None
    if target is not None:
        lease = _take_lease(
            session, m, agent, target, offline=offline, idempotent=idempotent, now=now
        )

    if idempotent:
        # Idempotent re-checkout — no status change, refresh nothing else.
        return CheckoutResult(
            task_id=task_id,
            checked_out_by=m.checked_out_by,
            checked_out_at=m.checked_out_at,
            expected_status_at_checkout=m.expected_status_at_checkout,
            new_status=m.status,
            idempotent=True,
            lease=lease,
        )

    m.checked_out_by = agent
    m.checked_out_at = now
    m.expected_status_at_checkout = m.status
    # ADO-156: чекаут всегда приземляется в каноническое написание. Раньше
    # задача из легаси `pending` получала легаси `in-progress` — так словарь
    # статусов и размножался: одно и то же состояние в двух написаниях,
    # выбор между которыми зависел от того, когда задачу создали.
    # Написание на ВХОДЕ по-прежнему любое: `expected` ниже сравнивается
    # через `normalise`, а дефолт `["todo", "pending"]` защищает ещё не
    # мигрированную БД.
    m.status = "in_progress"
    m.last_updated = now
    session.flush()
    if lease == checkout_lease.OFFLINE and m.uid:
        checkout_lease.mark_offline(session, project_id, m.uid, now)

    activity_service.emit_for_write(
        session,
        m.project_id,
        "task.checked_out",
        agent,
        scope_kind="task",
        scope_id=task_id,
        payload={
            "old_status": m.expected_status_at_checkout,
            "new_status": m.status,
        },
        summary=f"Task {task_id} checked out by {agent}",
    )
    if target is not None:
        remote = target[1]
        activity_service.emit_for_write(
            session,
            m.project_id,
            "task.checkout_leased",
            agent,
            scope_kind="task",
            scope_id=task_id,
            payload={"remote": remote, "offline": lease == checkout_lease.OFFLINE},
            summary=f"Task {task_id}: замок на {remote} ({lease})",
        )

    return CheckoutResult(
        task_id=task_id,
        checked_out_by=agent,
        checked_out_at=now,
        expected_status_at_checkout=m.expected_status_at_checkout,
        new_status=m.status,
        lease=lease,
    )


def _take_lease(
    session: Session,
    m: TaskModel,
    agent: str,
    target: tuple[str, str, str],
    *,
    offline: bool,
    idempotent: bool,
    now: datetime,
) -> str:
    """CLO-031: аренда до первой записи → ``"leased"`` или ``"offline"``.

    Аренда, взятая этим вызовом, снимается, если транзакция откатится.
    """
    slug, remote, project_uid = target
    task_uid = str(m.uid)
    marked = checkout_lease.is_marked(session, m.project_id, task_uid)
    if offline and (marked or not idempotent):
        return checkout_lease.OFFLINE
    holder = checkout_lease.Holder(
        task_id=m.task_id,
        agent=agent,
        replica=self_replica(session, m.project_id),
        acquired_at=m.checked_out_at if idempotent and m.checked_out_at else now,
    )
    _, etag, created = checkout_lease.claim_for_checkout(
        slug, remote, project_uid, task_uid, holder
    )
    if created:
        checkout_lease.schedule_rollback_drop(
            session, slug, remote, project_uid, task_uid, holder, etag
        )
    if marked:
        checkout_lease.unmark(session, m.project_id, task_uid)
    return checkout_lease.LEASED


def schedule_lease_release(
    session: Session, m: TaskModel, actor: str, *, agent: str | None, expired_ok: bool = False
) -> None:
    """CLO-031: аренда снимается после коммита; событие — в транзакции снятия замка."""
    if not m.uid:
        return
    remote = checkout_lease.schedule_release(
        session, m.project_id, m.uid, agent=agent, expired_ok=expired_ok
    )
    if remote is None:
        return
    activity_service.emit_for_write(
        session,
        m.project_id,
        "task.checkout_lease_released",
        actor,
        scope_kind="task",
        scope_id=m.task_id,
        payload={"remote": remote, "offline": False},
        summary=f"Task {m.task_id}: аренда замка на {remote} снимается",
    )


def release(
    session: Session,
    task_id: str,
    *,
    project_id: int,
    agent: str,
    force: bool = False,
) -> CheckoutResult:
    """Release the lock if held by ``agent`` (or any agent when ``force=True``).

    No status change. To complete the task, call ``task_complete`` separately;
    to revert to TODO, call ``task_update_status`` after release.

    With a remote (CLO-031) the lease of this replica is dropped after commit;
    ``force`` also drops a foreign lease past its own TTL.
    """
    m = _load_task(session, task_id, project_id=project_id)
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

    schedule_lease_release(session, m, agent, agent=m.checked_out_by, expired_ok=force)
    m.checked_out_by = None
    m.checked_out_at = None
    m.last_updated = datetime.now(UTC)
    session.flush()

    activity_service.emit_for_write(
        session,
        m.project_id,
        "task.released",
        agent,
        scope_kind="task",
        scope_id=task_id,
        payload={"status": m.status},
        summary=f"Task {task_id} released by {agent}",
    )

    return CheckoutResult(
        task_id=task_id,
        checked_out_by=None,
        checked_out_at=None,
        expected_status_at_checkout=m.expected_status_at_checkout,
        new_status=m.status,
    )


def has_active_checkout(session: Session, task_id: str, agent: str, *, project_id: int) -> bool:
    return _locked_by(session, task_id, project_id=project_id) == agent


def warn_if_no_checkout(
    session: Session, task_id: str, agent: str, *, project_id: int
) -> str | None:
    """PCA-921: Return a warning string if ``agent`` does not currently hold
    the lock for ``task_id``, otherwise None.

    Used by write-tools (task.complete / task.set_blocker / task.log_progress)
    to surface drift between MCP-driven mutations and the checkout discipline
    documented in proposal 06.  Logged + returned; never raises.
    """
    import logging

    locked_by = _locked_by(session, task_id, project_id=project_id)
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


def release_stale(
    session: Session,
    *,
    ttl_minutes: int = 30,
    project_id: int | None = None,
) -> list[str]:
    """Force-release locks older than ``ttl_minutes``; return released task_ids.

    ``project_id`` (ADO-192) ограничивает чистку одним проектом. Во встроенной
    ``<project>/.cod-doc/state.db`` разницы нет — проект там один. В
    hub-режиме есть: без фильтра ``cod-doc update -p <slug>`` снимал бы замки
    соседних проектов, живущих в той же БД. Молча снять чужой замок хуже, чем
    не снять свой: владелец чужого замка узнает об этом, только потеряв
    работу, а про несобранный мусор в своём проекте скажет следующий диагноз.

    ``None`` оставлен и остаётся умолчанием: функция задумана и как общая
    чистка всей БД, и её единственный такой вызывающий — оператор, а не
    проектная команда.

    С remote (CLO-031) после коммита снимается и аренда снятого замка: своей
    реплики — всегда, чужой — если истекла по её собственному ``ttl_minutes``.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=ttl_minutes)
    stmt = select(TaskModel).where(
        TaskModel.checked_out_at.is_not(None),
        TaskModel.checked_out_at < cutoff,
    )
    if project_id is not None:
        stmt = stmt.where(TaskModel.project_id == project_id)
    rows = list(session.execute(stmt).scalars())
    released: list[str] = []
    for m in rows:
        schedule_lease_release(session, m, "system", agent=None, expired_ok=True)
        m.checked_out_by = None
        m.checked_out_at = None
        m.last_updated = datetime.now(UTC)
        released.append(m.task_id)
    if released:
        session.flush()
    for m in rows:
        activity_service.emit_for_write(
            session,
            m.project_id,
            "task.released",
            "system",
            scope_kind="task",
            scope_id=m.task_id,
            payload={"status": m.status, "reason": "stale_lock_release"},
            summary=f"Task {m.task_id} released by stale-lock cleanup",
        )
    return released
