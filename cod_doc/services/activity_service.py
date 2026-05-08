"""ActivityEmitter + query functions — unified audit timeline (PCA-111, proposal 09).

All MCP write-tools call ``emit(session, project_id, kind, …)`` inside the
same transaction as their mutation. Because SQLite is single-writer, this is
safe without an outbox pattern.

Canonical ``kind`` values (partial list — see proposal 09):
    task.created / task.status_changed / task.blocker_added / task.blocker_cleared
    doc.created / doc.updated / doc.renamed / doc.drift_detected
    task_doc.updated
    master.updated
    link.synced / link.broken
    run.started / run.finished / run.failed
    approval.requested / approval.resolved / approval.cancelled / approval.expired

Public API
----------
- ``emit(session, project_id, kind, …)`` — insert one event row
- ``list_events(session, project_id, …)`` → paginated event list
- ``events_for_run(session, project_id, run_id)`` → list for one run
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, func

from cod_doc.infra.models import ActivityEventModel
from cod_doc.services.run_context import get_current_run_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _uuid7() -> str:
    """Generate a UUIDv7 (RFC 9562 §5.7) — time-sortable.

    Layout (128 bits total):
      unix_ts_ms (48) | ver=7 (4) | rand_a (12) | var=10 (2) | rand_b (62)

    Sortable by creation time without relying on database row_id.
    """
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits
    rand_a = (rand >> 64) & 0xFFF                 # 12 bits
    rand_b = rand & ((1 << 62) - 1)               # 62 bits

    val = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    hex_str = f"{val:032x}"
    return f"{hex_str[0:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:32]}"


def _make_id() -> str:
    """Generate a UUIDv7-formatted, time-sortable event ID (PCA-915)."""
    return _uuid7()


def emit(
    session: Session,
    project_id: int,
    kind: str,
    *,
    actor_kind: str = "system",
    actor_id: str | None = None,
    run_id: str | None = None,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    payload: dict[str, Any] | None = None,
    summary: str | None = None,
    ts: datetime | None = None,
) -> ActivityEventModel:
    """Insert a single activity event in the current session/transaction.

    ``run_id`` defaults to the active contextvar value (set by ``run_scope``).
    """
    event = ActivityEventModel(
        id=_make_id(),
        project_id=project_id,
        ts=ts or datetime.now(UTC),
        actor_kind=actor_kind,
        actor_id=actor_id,
        run_id=run_id if run_id is not None else get_current_run_id(),
        kind=kind,
        scope_kind=scope_kind,
        scope_id=scope_id,
        payload=payload or {},
        summary=summary,
    )
    session.add(event)
    return event


def list_events(
    session: Session,
    project_id: int,
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    kind: str | None = None,
    actor_kind: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated event list, newest first."""
    base = select(ActivityEventModel).where(ActivityEventModel.project_id == project_id)
    count_q = (
        select(func.count())
        .select_from(ActivityEventModel)
        .where(ActivityEventModel.project_id == project_id)
    )

    if scope_kind is not None:
        base = base.where(ActivityEventModel.scope_kind == scope_kind)
        count_q = count_q.where(ActivityEventModel.scope_kind == scope_kind)
    if scope_id is not None:
        base = base.where(ActivityEventModel.scope_id == scope_id)
        count_q = count_q.where(ActivityEventModel.scope_id == scope_id)
    if kind is not None:
        base = base.where(ActivityEventModel.kind == kind)
        count_q = count_q.where(ActivityEventModel.kind == kind)
    if actor_kind is not None:
        base = base.where(ActivityEventModel.actor_kind == actor_kind)
        count_q = count_q.where(ActivityEventModel.actor_kind == actor_kind)
    if since is not None:
        base = base.where(ActivityEventModel.ts >= since)
        count_q = count_q.where(ActivityEventModel.ts >= since)
    if until is not None:
        base = base.where(ActivityEventModel.ts <= until)
        count_q = count_q.where(ActivityEventModel.ts <= until)

    total = int(session.execute(count_q).scalar_one() or 0)
    rows = list(session.execute(
        base
        .order_by(ActivityEventModel.ts.desc(), ActivityEventModel.row_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars())

    return {
        "items": [_event_to_dict(e) for e in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def events_for_run(
    session: Session,
    project_id: int,
    run_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """All activity events for a single agent run, oldest first."""
    rows = session.execute(
        select(ActivityEventModel)
        .where(
            ActivityEventModel.project_id == project_id,
            ActivityEventModel.run_id == run_id,
        )
        .order_by(ActivityEventModel.ts.asc(), ActivityEventModel.row_id.asc())
        .limit(limit)
    ).scalars()
    return [_event_to_dict(e) for e in rows]


def _event_to_dict(e: ActivityEventModel) -> dict[str, Any]:
    return {
        "id": e.id,
        "ts": e.ts.isoformat() if e.ts else None,
        "actor_kind": e.actor_kind,
        "actor_id": e.actor_id,
        "run_id": e.run_id,
        "kind": e.kind,
        "scope_kind": e.scope_kind,
        "scope_id": e.scope_id,
        "payload": e.payload,
        "summary": e.summary,
    }
