"""OBI-001: TaskMetricsService — record + query per-task completion stats.

Called automatically from ``task_service.complete``; the actual UI lives in
the metrics dashboard (OBI-002).

Public API:
- ``record_on_complete(session, task_id)`` — idempotent; safe to re-run.
- ``list_for_project(session, project_id, *, since=None, limit=200)``.
- ``summary(session, project_id)`` — counts, p50/p90 by type.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.infra.models import RevisionModel, TaskMetricsModel, TaskModel

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


def _derive_state_durations(
    session: Session, task: TaskModel,
) -> tuple[float | None, float | None]:
    """Walk the task's status_changed revisions to compute time in
    ``in_progress`` and ``blocked``.

    Returns (in_progress_hours, blocked_hours). Either may be None if
    the revision timeline is too sparse to derive.
    """
    revs = list(session.execute(
        select(RevisionModel)
        .where(
            RevisionModel.entity_kind == "task",
            RevisionModel.entity_id == task.row_id,
        )
        .order_by(RevisionModel.at)
    ).scalars())
    if not revs:
        return (None, None)

    # Reconstruct a list of (status, entered_at) transitions from the
    # task's diff payload. We look for `diff.op == 'update_status'`-style
    # entries; the schema stores diff as TEXT/JSON, so be tolerant.
    transitions: list[tuple[str, datetime]] = [
        (task.status, _as_utc(task.created) or _as_utc(revs[0].at) or datetime.now(UTC)),
    ]
    for r in revs:
        diff_raw = r.diff or ""
        try:
            payload = json.loads(diff_raw) if diff_raw.startswith("{") else None
        except (ValueError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            continue
        new_status = payload.get("new_status") or payload.get("to")
        if not new_status:
            continue
        transitions.append((str(new_status), _as_utc(r.at) or datetime.now(UTC)))

    if len(transitions) < 2:
        return (None, None)

    # Sum durations spent in each canonical bucket. We don't replay the
    # full state machine here — just the two states we care about.
    end_ts = _as_utc(task.completed_at) or datetime.now(UTC)
    in_progress = 0.0
    blocked = 0.0
    for i, (status, entered_at) in enumerate(transitions):
        exit_at = transitions[i + 1][1] if i + 1 < len(transitions) else end_ts
        seconds = max(0.0, (exit_at - entered_at).total_seconds())
        canonical = status.replace("-", "_")
        if canonical == "in_progress":
            in_progress += seconds
        elif canonical == "blocked":
            blocked += seconds
    ip_h = round(in_progress / 3600.0, 4) if in_progress > 0 else None
    bl_h = round(blocked / 3600.0, 4) if blocked > 0 else None
    return (ip_h, bl_h)


# ----------------------------------------------------------------- #
# Public: record                                                     #
# ----------------------------------------------------------------- #


def record_on_complete(session: Session, task: TaskModel) -> TaskMetricsModel:
    """Insert or upsert a metrics row for a just-completed task.

    Idempotent: if a row already exists for ``task.row_id``, returns it
    unchanged. The hook in ``task_service.complete`` calls this — safe
    under re-completion / reopen-then-redo cycles.
    """
    if task.row_id is None:
        raise ValueError("task must have a row_id before recording metrics")

    existing = session.execute(
        select(TaskMetricsModel).where(TaskMetricsModel.task_id == task.row_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    completed_at = _as_utc(task.completed_at) or datetime.now(UTC)
    created = _as_utc(task.created) or completed_at  # paranoia for old rows
    duration_h = round((completed_at - created).total_seconds() / 3600.0, 4)
    if duration_h < 0:
        duration_h = 0.0
    ip_h, bl_h = _derive_state_durations(session, task)

    rev_count = session.execute(
        select(RevisionModel)
        .where(
            RevisionModel.entity_kind == "task",
            RevisionModel.entity_id == task.row_id,
        )
    ).all()

    row = TaskMetricsModel(
        task_id=task.row_id,
        project_id=task.project_id,
        completed_at=completed_at,
        duration_hours=duration_h,
        in_progress_hours=ip_h,
        blocked_hours=bl_h,
        revision_count=len(rev_count),
        commit_count=0,  # OBI-010 will backfill from commit_links
        priority=task.priority,
        type=task.type,
    )
    session.add(row)
    session.flush()
    return row


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
    stmt = (
        select(TaskMetricsModel)
        .where(TaskMetricsModel.project_id == project_id)
    )
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
            "completed": 0, "since": since.isoformat() if since else None,
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
        hour=0, minute=0, second=0, microsecond=0,
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
        out.append({
            "date": d,
            "count": len(vals),
            "p50_hours": _percentile(vals, 0.5) if vals else None,
        })
    return out
