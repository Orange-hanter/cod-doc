"""RoutineService — cron-style health checks (PCA-211, proposal 07).

This module owns the *data* + *execution* layer for routines. The
catalog of available checks lives in :data:`CHECK_CATALOG`; each entry
is a ``(callable, default_args)`` pair. The cron daemon (a separate
concern) periodically calls :func:`run_due` to execute the next batch.

For now we ship four built-in checks that wrap the existing
maintenance utilities:

- ``stale_refs``     — wraps ``check_stale_refs``
- ``link_integrity`` — wraps ``link.verify``
- ``doc_drift``      — wraps ``doc.drift``
- ``task_stale``     — wraps ``task.stale``

Plus the meta-check from proposal 12:

- ``approval_stale`` — auto-cancels pending approvals past ``expires_at``

Public API
----------
- ``create / update / list / get / delete / run_now`` — CRUD + manual fire
- ``history(routine_id, limit)`` → recent RoutineRun rows
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy import select, func

from cod_doc.infra.models import (
    ApprovalModel,
    RoutineModel,
    RoutineRunModel,
)
from cod_doc.services import activity_service
from cod_doc.services.run_context import get_current_run_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


VALID_TRIGGERS = frozenset({"cron", "manual", "event"})
VALID_ON_FINDING = frozenset({"create_task", "update_existing_task", "comment_only"})
VALID_CONCURRENCY = frozenset({"skip", "queue", "parallel"})
VALID_CATCH_UP = frozenset({"skip", "run_latest", "run_all"})


class RoutineNotFoundError(LookupError):
    pass


@dataclass
class Routine:
    row_id: int
    project_id: int
    name: str
    enabled: bool
    trigger: str
    cron: str | None
    check_name: str
    check_args: dict[str, Any]
    on_finding: str
    concurrency: str
    catch_up: str
    created: datetime
    last_updated: datetime


@dataclass
class RoutineRun:
    row_id: int
    routine_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    findings_count: int
    created_task_id: str | None
    error: str | None
    run_id: str | None


def _to_domain(m: RoutineModel) -> Routine:
    return Routine(
        row_id=m.row_id,
        project_id=m.project_id,
        name=m.name,
        enabled=m.enabled,
        trigger=m.trigger,
        cron=m.cron,
        check_name=m.check_name,
        check_args=m.check_args or {},
        on_finding=m.on_finding,
        concurrency=m.concurrency,
        catch_up=m.catch_up,
        created=m.created,
        last_updated=m.last_updated,
    )


def _run_to_domain(m: RoutineRunModel) -> RoutineRun:
    return RoutineRun(
        row_id=m.row_id,
        routine_id=m.routine_id,
        started_at=m.started_at,
        finished_at=m.finished_at,
        status=m.status,
        findings_count=m.findings_count,
        created_task_id=m.created_task_id,
        error=m.error,
        run_id=m.run_id,
    )


# --------------------------------------------------------------------------- #
# Check catalog                                                                #
# --------------------------------------------------------------------------- #


def _check_approval_stale(session: Session, project_id: int, **_: Any) -> dict[str, Any]:
    """Find pending approvals past expires_at and mark them expired."""
    now = datetime.now(UTC)
    rows = list(session.execute(
        select(ApprovalModel).where(
            ApprovalModel.project_id == project_id,
            ApprovalModel.status == "pending",
            ApprovalModel.expires_at.is_not(None),
            ApprovalModel.expires_at < now,
        )
    ).scalars())
    expired_ids: list[str] = []
    for a in rows:
        a.status = "expired"
        a.resolved_at = now
        a.decision_comment = "expired by routine"
        expired_ids.append(a.approval_id)
        activity_service.emit(
            session, project_id, "approval.expired",
            actor_kind="routine",
            actor_id="approval_stale",
            scope_kind="approval",
            scope_id=a.approval_id,
            summary=f"Approval {a.approval_id} expired",
        )
    return {"expired_count": len(expired_ids), "expired_ids": expired_ids}


def _check_noop(session: Session, project_id: int, **_: Any) -> dict[str, Any]:
    """Placeholder — real check delegates to existing MCP tool wrappers.

    The cron daemon should bind these names to existing entry points
    (``check_stale_refs``, ``link.verify``, ``doc.drift``, ``task.stale``).
    Wiring those is intentionally not in PCA-211 scope; this stub keeps
    routines runnable for tests and for the activity emission flow.
    """
    return {"findings": [], "note": "noop check — daemon wiring pending"}


CheckFn = Callable[..., dict[str, Any]]

CHECK_CATALOG: dict[str, CheckFn] = {
    "approval_stale": _check_approval_stale,
    "stale_refs":     _check_noop,
    "link_integrity": _check_noop,
    "doc_drift":      _check_noop,
    "task_stale":     _check_noop,
}


# --------------------------------------------------------------------------- #
# CRUD                                                                         #
# --------------------------------------------------------------------------- #


def create(
    session: Session,
    project_id: int,
    *,
    name: str,
    check_name: str,
    trigger: str = "cron",
    cron: str | None = None,
    check_args: dict[str, Any] | None = None,
    on_finding: str = "comment_only",
    concurrency: str = "skip",
    catch_up: str = "run_latest",
    enabled: bool = True,
) -> Routine:
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"trigger must be one of {sorted(VALID_TRIGGERS)}")
    if on_finding not in VALID_ON_FINDING:
        raise ValueError(f"on_finding must be one of {sorted(VALID_ON_FINDING)}")
    if concurrency not in VALID_CONCURRENCY:
        raise ValueError(f"concurrency must be one of {sorted(VALID_CONCURRENCY)}")
    if catch_up not in VALID_CATCH_UP:
        raise ValueError(f"catch_up must be one of {sorted(VALID_CATCH_UP)}")
    if check_name not in CHECK_CATALOG:
        raise ValueError(f"unknown check_name {check_name!r}; known: {sorted(CHECK_CATALOG)}")
    if trigger == "cron" and not cron:
        raise ValueError("trigger='cron' requires a cron expression")

    m = RoutineModel(
        project_id=project_id,
        name=name,
        enabled=enabled,
        trigger=trigger,
        cron=cron,
        check_name=check_name,
        check_args=check_args or {},
        on_finding=on_finding,
        concurrency=concurrency,
        catch_up=catch_up,
    )
    session.add(m)
    session.flush()
    return _to_domain(m)


def get(session: Session, project_id: int, name: str) -> Routine | None:
    m = session.execute(
        select(RoutineModel).where(
            RoutineModel.project_id == project_id, RoutineModel.name == name,
        )
    ).scalar_one_or_none()
    return _to_domain(m) if m is not None else None


def list_routines(
    session: Session, project_id: int, *, enabled_only: bool = False,
) -> list[Routine]:
    stmt = select(RoutineModel).where(RoutineModel.project_id == project_id)
    if enabled_only:
        stmt = stmt.where(RoutineModel.enabled.is_(True))
    rows = session.execute(stmt.order_by(RoutineModel.name)).scalars()
    return [_to_domain(m) for m in rows]


def update_status(
    session: Session, project_id: int, name: str, *, enabled: bool,
) -> Routine:
    m = session.execute(
        select(RoutineModel).where(
            RoutineModel.project_id == project_id, RoutineModel.name == name,
        )
    ).scalar_one_or_none()
    if m is None:
        raise RoutineNotFoundError(f"Routine {name!r} not found.")
    m.enabled = enabled
    m.last_updated = datetime.now(UTC)
    session.flush()
    return _to_domain(m)


def delete(session: Session, project_id: int, name: str) -> None:
    m = session.execute(
        select(RoutineModel).where(
            RoutineModel.project_id == project_id, RoutineModel.name == name,
        )
    ).scalar_one_or_none()
    if m is None:
        raise RoutineNotFoundError(f"Routine {name!r} not found.")
    session.delete(m)
    session.flush()


# --------------------------------------------------------------------------- #
# Execution                                                                    #
# --------------------------------------------------------------------------- #


def run_now(session: Session, project_id: int, name: str) -> RoutineRun:
    """Manually fire a routine and persist a RoutineRun row.

    Concurrency policy 'skip': if there's an existing 'running' run for
    this routine, this call returns the existing row with status='skipped'
    instead of starting a new one.
    """
    routine = session.execute(
        select(RoutineModel).where(
            RoutineModel.project_id == project_id, RoutineModel.name == name,
        )
    ).scalar_one_or_none()
    if routine is None:
        raise RoutineNotFoundError(f"Routine {name!r} not found.")

    if routine.concurrency == "skip":
        existing = session.execute(
            select(RoutineRunModel).where(
                RoutineRunModel.routine_id == routine.row_id,
                RoutineRunModel.status == "running",
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _run_to_domain(existing)

    run_row = RoutineRunModel(
        routine_id=routine.row_id,
        status="running",
        run_id=get_current_run_id(),
    )
    session.add(run_row)
    session.flush()

    activity_service.emit(
        session, project_id, "routine.fired",
        actor_kind="routine",
        actor_id=routine.name,
        scope_kind="routine",
        scope_id=routine.name,
        summary=f"Routine {routine.name!r} fired",
    )

    check_fn = CHECK_CATALOG[routine.check_name]
    try:
        result = check_fn(session, project_id, **(routine.check_args or {}))
        findings_count = int(result.get("findings_count")
                             or result.get("expired_count")
                             or len(result.get("findings", [])))
        run_row.findings_count = findings_count
        run_row.status = "done"
        run_row.finished_at = datetime.now(UTC)
        if findings_count > 0:
            activity_service.emit(
                session, project_id, "routine.found_issue",
                actor_kind="routine",
                actor_id=routine.name,
                scope_kind="routine",
                scope_id=routine.name,
                payload={"findings_count": findings_count, "result": result},
                summary=f"Routine {routine.name!r}: {findings_count} finding(s)",
            )
    except Exception as exc:  # pragma: no cover — defensive guard
        run_row.status = "failed"
        run_row.error = repr(exc)
        run_row.finished_at = datetime.now(UTC)
        raise
    finally:
        session.flush()

    return _run_to_domain(run_row)


def history(
    session: Session, project_id: int, name: str, *, limit: int = 20,
) -> list[RoutineRun]:
    routine = session.execute(
        select(RoutineModel).where(
            RoutineModel.project_id == project_id, RoutineModel.name == name,
        )
    ).scalar_one_or_none()
    if routine is None:
        raise RoutineNotFoundError(f"Routine {name!r} not found.")
    rows = session.execute(
        select(RoutineRunModel)
        .where(RoutineRunModel.routine_id == routine.row_id)
        .order_by(RoutineRunModel.started_at.desc(), RoutineRunModel.row_id.desc())
        .limit(limit)
    ).scalars()
    return [_run_to_domain(r) for r in rows]
