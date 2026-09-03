"""ActivityEmitter + query functions — unified audit timeline (PCA-111, proposal 09).

All MCP write-tools call ``emit(session, project_id, kind, …)`` inside the
same transaction as their mutation. Because SQLite is single-writer, this is
safe without an outbox pattern.

Canonical ``kind`` values (partial list — see proposal 09 / PCA-912):
    task.created / task.status_changed / task.completed / task.blocker_added /
    task.blocker_cleared / task.dependency_removed / task.checked_out / task.released
    doc.created / doc.updated / doc.renamed / doc.deleted / doc.status_changed /
    doc.section_added / doc.section_updated
    task_doc.created / task_doc.updated / task_doc.reverted
    adr.created / adr.updated / adr.deprecated / adr.superseded / adr.diagram_added /
    adr.task_linked
    story.created / story.status_changed / story.criterion_added /
    story.criterion_met / story.linked
    comment.created / comment.status_changed / comment.deleted
    link.synced / link.resolved / link.verified
    approval.requested / approval.resolved / approval.cancelled
    commit_link.imported
    repo_index.scanned
    run.started / run.finished / run.failed

Public API
----------
- ``emit(session, project_id, kind, …)`` — insert one event row
- ``emit_for_write(session, project_id, kind, author, …)`` — insert one event row
  for a write mutation, deriving ``actor_kind`` from ``author``.
- ``write_revision_and_emit_event(session, …)`` — append a revision and emit an
  activity event atomically in the current transaction.
- ``list_events(session, project_id, …)`` → paginated event list
- ``events_for_run(session, project_id, run_id)`` → list for one run
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from cod_doc.infra.models import ActivityEventModel
from cod_doc.services import revision_service as rev
from cod_doc.services.run_context import get_current_run_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import EntityKind, Revision


def _uuid7() -> str:
    """Generate a UUIDv7 (RFC 9562 §5.7) — time-sortable.

    Layout (128 bits total):
      unix_ts_ms (48) | ver=7 (4) | rand_a (12) | var=10 (2) | rand_b (62)

    Sortable by creation time without relying on database row_id.
    """
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits
    rand_a = (rand >> 64) & 0xFFF  # 12 bits
    rand_b = rand & ((1 << 62) - 1)  # 62 bits

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


def _actor_kind_for_author(author: str) -> str:
    """Derive an activity-event actor_kind from a mutation author string.

    - ``agent:*`` / ``agent-…``      → ``agent``
    - ``orchestrator:*`` / ``orchestrator-…`` → ``orchestrator``
    - ``mcp``                        → ``system``
    - anything else (``human:*``, ``cli``, etc.) → ``human``
    """
    if author.startswith("agent"):
        return "agent"
    if author.startswith("orchestrator"):
        return "orchestrator"
    if author == "mcp":
        return "system"
    return "human"


def emit_for_write(
    session: Session,
    project_id: int,
    kind: str,
    author: str,
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    payload: dict[str, Any] | None = None,
    summary: str | None = None,
) -> ActivityEventModel:
    """Emit an activity event for a write mutation.

    Derives ``actor_kind`` from ``author`` via :func:`_actor_kind_for_author`.
    Errors are **not** swallowed: call this inside the same transaction as the
    mutation so the audit row is atomic with the write (proposal 09 / PCA-912).
    """
    return emit(
        session,
        project_id,
        kind,
        actor_kind=_actor_kind_for_author(author),
        actor_id=author,
        scope_kind=scope_kind,
        scope_id=scope_id,
        payload=payload,
        summary=summary,
    )


def write_revision_and_emit_event(
    session: Session,
    *,
    project_id: int,
    entity_kind: EntityKind,
    entity_id: int,
    author: str,
    diff: str,
    reason: str | None,
    commit_sha: str | None = None,
    expected_parent_revision_id: str | object | None = rev.NO_PARENT_CHECK,
    activity_kind: str,
    activity_scope_kind: str | None = None,
    activity_scope_id: str | None = None,
    activity_payload: dict[str, Any] | None = None,
    activity_summary: str | None = None,
) -> tuple[Revision, ActivityEventModel]:
    """Append a revision and emit an activity event in one atomic unit.

    Both operations run inside the caller's transaction. The activity event
    uses ``actor_kind`` derived from ``author``. Errors from either operation
    are **not** swallowed (PCA-912).
    """
    revision = rev.write(
        session,
        project_id=project_id,
        entity_kind=entity_kind,
        entity_id=entity_id,
        author=author,
        diff=diff,
        reason=reason,
        commit_sha=commit_sha,
        expected_parent_revision_id=expected_parent_revision_id,
    )
    event = emit_for_write(
        session,
        project_id,
        activity_kind,
        author,
        scope_kind=activity_scope_kind,
        scope_id=activity_scope_id,
        payload=activity_payload,
        summary=activity_summary,
    )
    return revision, event


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
    rows = list(
        session.execute(
            base.order_by(ActivityEventModel.ts.desc(), ActivityEventModel.row_id.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )

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
