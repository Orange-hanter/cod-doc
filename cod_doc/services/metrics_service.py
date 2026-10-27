"""OBI-001: TaskMetricsService — record + query per-task completion stats.

Called automatically from ``task_service.complete``; the actual UI lives in
the metrics dashboard (OBI-002).

Public API:
- ``record_on_complete(session, task_id)`` — idempotent; safe to re-run.
- ``rebuild(session, *, project_id, author, dry_run=False)`` — FLW-003 backfill:
  recompute every closed task's row from the transition journal.
- ``list_for_project(session, project_id, *, since=None, limit=200)``.
- ``summary(session, project_id)`` — counts, p50/p90 by type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from cod_doc.domain.entities import canonical_task_status
from cod_doc.infra.models import ActivityEventModel, RevisionModel, TaskMetricsModel, TaskModel
from cod_doc.services import activity_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ----------------------------------------------------------------- #
# Helpers                                                            #
# ----------------------------------------------------------------- #


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; tag them as UTC so arithmetic works."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _hours_between(a: datetime | None, b: datetime | None) -> float | None:
    a, b = _as_utc(a), _as_utc(b)
    if a is None or b is None:
        return None
    delta = b - a
    return round(delta.total_seconds() / 3600.0, 4)


def _percentile(values: list[float], p: float) -> float | None:
    """Linear interpolation percentile. ``p`` ∈ [0,1]. None for empty list."""
    if not values:
        return None
    sv = sorted(values)
    if len(sv) == 1:
        return round(sv[0], 4)
    rank = p * (len(sv) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sv) - 1)
    frac = rank - lo
    return round(sv[lo] + (sv[hi] - sv[lo]) * frac, 4)


#: Переходы статуса в журнале событий (RFC 33 §3.1): вид события → откуда брать
#: новый статус. ``task.completed`` всегда ведёт в done.
_TRANSITION_KINDS = ("task.checked_out", "task.status_changed", "task.completed")
_IN_PROGRESS = "in_progress"
_BLOCKED = "blocked"
_DONE = "done"
_SECONDS_PER_HOUR = 3600.0


def _transitions(session: Session, task: TaskModel) -> list[tuple[str, datetime]]:
    """Таймлайн ``(статус, с какого момента)`` задачи из ``activity_event``.

    FLW-003: переходы — журнал событий, а не диффы ревизий. ``task.status_changed``
    без ``old_status`` — дубль MCP-обёртки до FLW-001 — пропускается: он
    повторяет переход сервиса или несёт статус без исходного. Легаси-написания
    (``in-progress``, ``pending``) сводятся к каноническим.
    """
    rows = session.execute(
        select(ActivityEventModel.kind, ActivityEventModel.ts, ActivityEventModel.payload)
        .where(
            ActivityEventModel.project_id == task.project_id,
            ActivityEventModel.scope_kind == "task",
            ActivityEventModel.scope_id == task.task_id,
            ActivityEventModel.kind.in_(_TRANSITION_KINDS),
        )
        .order_by(ActivityEventModel.ts, ActivityEventModel.row_id)
    ).all()
    out: list[tuple[str, datetime]] = []
    for kind, ts, payload in rows:
        data = payload if isinstance(payload, dict) else {}
        if kind == "task.completed":
            status = _DONE
        elif kind == "task.checked_out":
            status = str(data.get("new_status") or _IN_PROGRESS)
        elif "old_status" in data and data.get("new_status"):
            status = str(data["new_status"])
        else:
            continue
        at = _as_utc(ts)
        if at is not None:
            out.append((canonical_task_status(status), at))
    return out


def _derive_state_durations(
    session: Session,
    task: TaskModel,
) -> tuple[float | None, float | None]:
    """Часы в ``in_progress`` и ``blocked`` по журналу переходов (FLW-003).

    Returns ``(in_progress_hours, blocked_hours)``; ``None`` — задача в этом
    состоянии не была (или событий о ней нет: задачи старше журнала).
    Последний интервал закрывается ``completed_at`` (или последним событием).
    """
    transitions = _transitions(session, task)
    if not transitions:
        return (None, None)
    end_ts = _as_utc(task.completed_at) or transitions[-1][1]
    spent = {_IN_PROGRESS: 0.0, _BLOCKED: 0.0}
    for i, (status, entered_at) in enumerate(transitions):
        exit_at = transitions[i + 1][1] if i + 1 < len(transitions) else end_ts
        if status in spent:
            spent[status] += max(0.0, (exit_at - entered_at).total_seconds())
    # Было в состоянии (> 0 с) — значение, даже если округлилось до 0.0; не было — None.
    ip_h = round(spent[_IN_PROGRESS] / _SECONDS_PER_HOUR, 4) if spent[_IN_PROGRESS] > 0 else None
    bl_h = round(spent[_BLOCKED] / _SECONDS_PER_HOUR, 4) if spent[_BLOCKED] > 0 else None
    return (ip_h, bl_h)


@dataclass(frozen=True, slots=True)
class _Values:
    completed_at: datetime
    duration_hours: float
    in_progress_hours: float | None
    blocked_hours: float | None
    revision_count: int
    has_events: bool


def _values(session: Session, task: TaskModel) -> _Values:
    completed_at = _as_utc(task.completed_at) or datetime.now(UTC)
    created = _as_utc(task.created) or completed_at  # paranoia for old rows
    duration_h = max(0.0, round((completed_at - created).total_seconds() / _SECONDS_PER_HOUR, 4))
    ip_h, bl_h = _derive_state_durations(session, task)
    revisions = session.execute(
        select(func.count(RevisionModel.row_id)).where(
            RevisionModel.entity_kind == "task",
            RevisionModel.entity_id == task.row_id,
        )
    ).scalar_one()
    # Есть ли переходы в журнале — тот же запрос, что внутри _derive_state_durations:
    # без событий обе длительности None по построению.
    has_events = ip_h is not None or bl_h is not None or bool(_transitions(session, task))
    return _Values(completed_at, duration_h, ip_h, bl_h, int(revisions), has_events)


# ----------------------------------------------------------------- #
# Public: record                                                     #
# ----------------------------------------------------------------- #


def record_on_complete(session: Session, task: TaskModel) -> TaskMetricsModel:
    """Insert or upsert a metrics row for a just-completed task.

    Idempotent: if a row already exists for ``task.row_id``, returns it
    unchanged. The hook in ``task_service.complete`` calls this — safe
    under re-completion / reopen-then-redo cycles. Пересчёт существующих
    строк — :func:`rebuild` (FLW-003).
    """
    if task.row_id is None:
        raise ValueError("task must have a row_id before recording metrics")

    existing = session.execute(
        select(TaskMetricsModel).where(TaskMetricsModel.task_id == task.row_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    values = _values(session, task)
    row = TaskMetricsModel(
        task_id=task.row_id,
        project_id=task.project_id,
        completed_at=values.completed_at,
        duration_hours=values.duration_hours,
        in_progress_hours=values.in_progress_hours,
        blocked_hours=values.blocked_hours,
        revision_count=values.revision_count,
        commit_count=0,  # OBI-010 will backfill from commit_links
        priority=task.priority,
        type=task.type,
    )
    session.add(row)
    session.flush()
    return row


@dataclass(frozen=True, slots=True)
class RebuildReport:
    """Итог :func:`rebuild`: что пересчитано и почему часть строк пуста."""

    tasks: int  # закрытых задач проекта
    created: int  # строк task_metrics заведено (задачи закрыты до OBI-001)
    updated: int  # строк, у которых изменились значения
    with_in_progress: int  # строк с непустым in_progress_hours после пересчёта
    without_events: int  # задач без единого события-перехода (старше журнала)


def rebuild(
    session: Session,
    *,
    project_id: int,
    author: str,
    dry_run: bool = False,
) -> RebuildReport:
    """Пересчитать ``task_metrics`` всех закрытых задач проекта (FLW-003, бэкфилл).

    Идемпотентно: второй прогон без новых событий ничего не меняет. Строки нет —
    заводится, есть — значения переписываются из журнала переходов.
    ``task_metrics`` — local-таблица (не синхронизируется), ревизии у неё нет;
    след прогона — одно событие ``metrics.rebuilt`` со счётчиками (ADO-040,
    ``emit_for_write``). ``dry_run`` считает отчёт, ничего не записывая.
    """
    tasks = session.execute(
        select(TaskModel).where(TaskModel.project_id == project_id, TaskModel.status == _DONE)
    ).scalars()
    existing = {
        row.task_id: row
        for row in session.execute(
            select(TaskMetricsModel).where(TaskMetricsModel.project_id == project_id)
        ).scalars()
    }
    total = created = updated = with_ip = without_events = 0
    for task in tasks:
        total += 1
        values = _values(session, task)
        if values.in_progress_hours is not None:
            with_ip += 1
        if not values.has_events:
            without_events += 1
        row = existing.get(task.row_id)
        if row is None:
            created += 1
            if not dry_run:
                session.add(
                    TaskMetricsModel(
                        task_id=task.row_id,
                        project_id=task.project_id,
                        completed_at=values.completed_at,
                        duration_hours=values.duration_hours,
                        in_progress_hours=values.in_progress_hours,
                        blocked_hours=values.blocked_hours,
                        revision_count=values.revision_count,
                        commit_count=0,
                        priority=task.priority,
                        type=task.type,
                    )
                )
            continue
        fresh = {
            "duration_hours": values.duration_hours,
            "in_progress_hours": values.in_progress_hours,
            "blocked_hours": values.blocked_hours,
            "revision_count": values.revision_count,
        }
        if any(getattr(row, k) != v for k, v in fresh.items()):
            updated += 1
            if not dry_run:
                for k, v in fresh.items():
                    setattr(row, k, v)
    report = RebuildReport(total, created, updated, with_ip, without_events)
    if not dry_run:
        session.flush()
        activity_service.emit_for_write(
            session,
            project_id,
            "metrics.rebuilt",
            author,
            scope_kind="project",
            scope_id=str(project_id),
            payload={
                "tasks": total,
                "created": created,
                "updated": updated,
                "with_in_progress": with_ip,
                "without_events": without_events,
            },
            summary=(
                f"task_metrics rebuilt: {total} tasks, {created} created, {updated} updated, "
                f"{with_ip} with in_progress_hours"
            ),
        )
    return report


# ----------------------------------------------------------------- #
# Public: read                                                       #
# ----------------------------------------------------------------- #


def list_for_project(
    session: Session,
    project_id: int,
    *,
    since: datetime | None = None,
    limit: int = 200,
) -> list[TaskMetricsModel]:
    stmt = select(TaskMetricsModel).where(TaskMetricsModel.project_id == project_id)
    if since is not None:
        stmt = stmt.where(TaskMetricsModel.completed_at >= since)
    stmt = stmt.order_by(TaskMetricsModel.completed_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def summary(
    session: Session,
    project_id: int,
    *,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate stats: count + p50/p90 duration by type."""
    rows = list_for_project(session, project_id, since=since, limit=10_000)
    if not rows:
        return {
            "completed": 0,
            "since": since.isoformat() if since else None,
            "duration_hours": {"p50": None, "p90": None, "mean": None},
            "by_type": {},
        }

    durations = [r.duration_hours for r in rows]
    by_type: dict[str, list[float]] = {}
    for r in rows:
        by_type.setdefault(r.type, []).append(r.duration_hours)

    out: dict[str, Any] = {
        "completed": len(rows),
        "since": since.isoformat() if since else None,
        "duration_hours": {
            "p50": _percentile(durations, 0.5),
            "p90": _percentile(durations, 0.9),
            "mean": round(sum(durations) / len(durations), 4),
        },
        "by_type": {
            ttype: {
                "n": len(vals),
                "p50": _percentile(vals, 0.5),
                "p90": _percentile(vals, 0.9),
            }
            for ttype, vals in sorted(by_type.items())
        },
    }
    return out


def sparkline_buckets(
    session: Session,
    project_id: int,
    *,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Group completions into per-day buckets for the last ``days`` days.

    Returns list of ``{date: 'YYYY-MM-DD', count: N, p50_hours: float | None}``,
    oldest first. Empty days are represented with count=0.
    """
    now = datetime.now(UTC)
    start = (now - timedelta(days=days - 1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    rows = list_for_project(session, project_id, since=start, limit=10_000)
    buckets: dict[str, list[float]] = {}
    for r in rows:
        key = r.completed_at.date().isoformat()
        buckets.setdefault(key, []).append(r.duration_hours)

    out: list[dict[str, Any]] = []
    for i in range(days):
        d = (start + timedelta(days=i)).date().isoformat()
        vals = buckets.get(d, [])
        out.append(
            {
                "date": d,
                "count": len(vals),
                "p50_hours": _percentile(vals, 0.5) if vals else None,
            }
        )
    return out
