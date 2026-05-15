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

import re as _re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from sqlalchemy import select

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


def _get_project_root(session: Session, project_id: int) -> Path | None:
    """Return the on-disk root for a project_id via the project.root_path column."""
    from pathlib import Path

    from cod_doc.infra.models.project import ProjectModel

    proj_model = session.get(ProjectModel, project_id)
    if proj_model is None:
        return None
    return Path(proj_model.root_path).expanduser().resolve()


def _check_stale_refs(session: Session, project_id: int, **_: Any) -> dict[str, Any]:
    """PCA-920: Scan MASTER.md hybrid references; report stale/missing files.

    Delegates to the same logic as the ``check_stale_refs`` MCP tool.
    """
    from cod_doc.core.hash_calc import LINK_PATTERN, calc_hash, check_hash

    root = _get_project_root(session, project_id)
    if root is None:
        return {"findings": [], "note": "project not found"}

    master_path = root / "MASTER.md"
    content = master_path.read_text(encoding="utf-8") if master_path.exists() else ""
    findings: list[dict[str, Any]] = []

    for m in LINK_PATTERN.finditer(content):
        rel = m.group("path").lstrip("/")
        expected = m.group("hash")
        target = root / rel
        if not target.exists():
            findings.append({"path": rel, "status": "BROKEN", "expected": expected})
        elif not check_hash(target, expected):
            actual = calc_hash(target)
            findings.append({"path": rel, "status": "STALE", "expected": expected, "actual": actual})

    return {"findings": findings, "findings_count": len(findings)}


def _check_link_integrity(
    session: Session,
    project_id: int,
    *,
    limit: int = 500,
    **_: Any,
) -> dict[str, Any]:
    """PCA-920: Verify resolved links for all sections; report broken ones.

    Delegates to ``link_service.verify_section`` for each section.
    """
    from sqlalchemy import select as _select

    from cod_doc.infra.models.documents import DocumentModel, SectionModel
    from cod_doc.services import link_service

    sec_ids = session.execute(
        _select(SectionModel.row_id)
        .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
        .where(DocumentModel.project_id == project_id)
        .limit(limit)
    ).scalars().all()

    total_broken = 0
    broken_detail: list[dict[str, Any]] = []
    for sid in sec_ids:
        report = link_service.verify_section(session, int(sid))
        if report.broken:
            broken_detail.append({"section_id": sid, "broken": report.broken})
            total_broken += report.broken

    return {
        "findings": broken_detail,
        "findings_count": total_broken,
        "sections_checked": len(sec_ids),
    }


def _check_doc_drift(
    session: Session,
    project_id: int,
    *,
    limit: int = 200,
    **_: Any,
) -> dict[str, Any]:
    """PCA-920: Detect drift for all documents; report stale/missing exports.

    Delegates to ``projection_service.detect_drift``.
    """
    from cod_doc.services import doc_service, projection_service

    root = _get_project_root(session, project_id)
    all_docs = doc_service.list_for_project(session, project_id)[:limit]
    findings: list[dict[str, Any]] = []
    for doc in all_docs:
        if doc.row_id is None or root is None:
            continue
        try:
            report = projection_service.detect_drift(session, doc.row_id, root_path=root)
            if report.status.value not in ("in_sync", "no_export"):
                findings.append({"doc_key": doc.doc_key, "status": report.status.value})
        except Exception:
            pass

    return {"findings": findings, "findings_count": len(findings)}


def _check_task_stale(
    session: Session,
    project_id: int,
    *,
    threshold_hours: float = 24.0,
    **_: Any,
) -> dict[str, Any]:
    """PCA-920: List in-progress tasks idle longer than threshold_hours.

    Delegates to ``task_service.list_stale_in_progress``.
    """
    from cod_doc.services import task_service

    stale = task_service.list_stale_in_progress(
        session, project_id, threshold_hours=threshold_hours
    )
    findings = [{"task_id": t.task_id, "title": t.title} for t in stale]
    return {"findings": findings, "findings_count": len(findings)}


CheckFn = Callable[..., dict[str, Any]]

CHECK_CATALOG: dict[str, CheckFn] = {
    "approval_stale": _check_approval_stale,
    "stale_refs":     _check_stale_refs,
    "link_integrity": _check_link_integrity,
    "doc_drift":      _check_doc_drift,
    "task_stale":     _check_task_stale,
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
            # PCA-922: on_finding policy
            if routine.on_finding == "update_existing_task":
                created_task_id = _update_or_create_finding_task(
                    session, project_id, routine, result,
                )
                if created_task_id:
                    run_row.created_task_id = created_task_id
    except Exception as exc:  # pragma: no cover — defensive guard
        run_row.status = "failed"
        run_row.error = repr(exc)
        run_row.finished_at = datetime.now(UTC)
        raise
    finally:
        session.flush()

    return _run_to_domain(run_row)


# --------------------------------------------------------------------------- #
# Scheduler tick (PCA-919)                                                     #
# --------------------------------------------------------------------------- #

_EVERY_N_MINUTES = _re.compile(r"^\*/(\d+)\s")      # */15 * * * *
_EVERY_N_HOURS   = _re.compile(r"^0\s\*/(\d+)\s")   # 0 */2 * * *
_DAILY           = _re.compile(r"^0\s0\s")           # 0 0 * * *


def _cron_interval_minutes(cron: str | None) -> int:
    """Parse a simple cron expression into an interval in minutes.

    Supported patterns (no external library required):
    - ``*/N * * * *``   → every N minutes
    - ``0 */N * * *``   → every N hours
    - ``0 0 * * *``     → every 1440 minutes (daily)

    Unknown expressions default to 60 minutes.
    """
    if not cron:
        return 60
    if m := _EVERY_N_MINUTES.match(cron):
        return max(1, int(m.group(1)))
    if m := _EVERY_N_HOURS.match(cron):
        return max(1, int(m.group(1))) * 60
    if _DAILY.match(cron):
        return 1440
    return 60  # safe default for unrecognised patterns


def tick(session: Session, project_id: int) -> list[str]:
    """PCA-919: Fire all overdue cron routines for a project.

    Called once per daemon cycle.  For each enabled routine with
    ``trigger='cron'``, compares the last ``started_at`` with the
    interval derived from the ``cron`` field.  Fires via ``run_now``
    when the interval has elapsed (or the routine has never run).

    Returns the list of routine names that were fired.
    """
    now = datetime.now(UTC)
    fired: list[str] = []

    routines = list(session.execute(
        select(RoutineModel).where(
            RoutineModel.project_id == project_id,
            RoutineModel.trigger == "cron",
            RoutineModel.enabled.is_(True),
        )
    ).scalars())

    for routine in routines:
        interval = timedelta(minutes=_cron_interval_minutes(routine.cron))

        last_run = session.execute(
            select(RoutineRunModel.started_at)
            .where(RoutineRunModel.routine_id == routine.row_id)
            .order_by(RoutineRunModel.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if last_run is not None and (now - last_run) < interval:
            continue  # not yet due

        try:
            run_now(session, project_id, routine.name)
            fired.append(routine.name)
        except Exception:
            pass  # run_now handles its own error logging / status

    return fired


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


# --------------------------------------------------------------------------- #
# on_finding=update_existing_task helper (PCA-922)                              #
# --------------------------------------------------------------------------- #


def _signature_for_routine(routine_name: str) -> str:
    """Stable signature embedded in finding-task descriptions for dedup."""
    return f"<!-- routine:{routine_name} -->"


def _update_or_create_finding_task(
    session: Session,
    project_id: int,
    routine: RoutineModel,
    result: dict[str, Any],
) -> str | None:
    """PCA-922: Find an open task with the routine's signature; update it.

    If no such task exists, create one. The signature is a hidden HTML
    comment in the description so it survives re-renders without affecting
    visible text.

    Returns the task_id of the touched task, or None on failure.
    """
    from sqlalchemy import select as _select

    from cod_doc.domain.entities import Priority, TaskType
    from cod_doc.infra.models import TaskModel
    from cod_doc.services import task_service

    sig = _signature_for_routine(routine.name)
    findings = result.get("findings", [])
    summary = (
        f"Routine `{routine.name}` reported {len(findings)} finding(s).\n\n"
        f"Latest result: {findings[:5]!r}\n\n{sig}"
    )

    # Look for an existing OPEN task with the signature in description.
    open_statuses = ("pending", "todo", "in-progress", "in_progress", "in_review", "blocked")
    existing = session.execute(
        _select(TaskModel)
        .where(
            TaskModel.project_id == project_id,
            TaskModel.status.in_(open_statuses),
            TaskModel.description.like(f"%{sig}%"),
        )
        .order_by(TaskModel.row_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if existing is not None:
        existing.description = summary
        existing.last_updated = datetime.now(UTC)
        session.flush()
        activity_service.emit(
            session, project_id, "task.updated_by_routine",
            actor_kind="routine",
            actor_id=routine.name,
            scope_kind="task",
            scope_id=existing.task_id,
            summary=f"Routine {routine.name!r} updated open task {existing.task_id}",
        )
        return existing.task_id

    # No open task — create one.  Pick the routine's plan/section by latest task
    # in the project (best-effort) so the new task lands somewhere sensible.
    fallback = session.execute(
        _select(TaskModel)
        .where(TaskModel.project_id == project_id)
        .order_by(TaskModel.row_id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if fallback is None:
        return None

    try:
        new_task = task_service.create(
            session,
            project_id=project_id,
            plan_id=fallback.plan_id,
            section_id=fallback.section_id,
            title=f"Routine finding: {routine.name}",
            type=TaskType.CHORE,
            priority=Priority.MEDIUM,
            description=summary,
            author=f"routine:{routine.name}",
            id_prefix="ROU",
        )
        return new_task.task_id
    except Exception:
        return None
