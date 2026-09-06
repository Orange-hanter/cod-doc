"""Read/dismiss queries over the ``finding`` table (RFC 22 §3.2, SYM-006D)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import actor_kind_for_author
from cod_doc.infra.models import FindingModel
from cod_doc.services import activity_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FINDING_STATUS_DISMISSED = "dismissed"
DEFAULT_LIST_LIMIT = 100


def finding_to_dict(f: FindingModel) -> dict[str, Any]:
    """Render a FindingModel row as a plain dict for MCP/JSON return."""
    return {
        "finding_id": f.row_id,
        "finding_uid": f.finding_uid,
        "source": f.source,
        "source_ref": f.source_ref,
        "fingerprint": f.fingerprint,
        "severity": f.severity,
        "kind": f.kind,
        "title": f.title,
        "body": f.body,
        "path": f.path,
        "line": f.line,
        "status": f.status,
        "confidence": f.confidence,
        "times_seen": f.times_seen,
        "first_seen_at": f.first_seen_at.isoformat() if f.first_seen_at else None,
        "last_seen_at": f.last_seen_at.isoformat() if f.last_seen_at else None,
        "promoted_task_id": f.promoted_task_id,
    }


def _get_model(session: Session, project_id: int, finding_uid: str) -> FindingModel | None:
    f = session.execute(
        select(FindingModel).where(FindingModel.finding_uid == finding_uid)
    ).scalar_one_or_none()
    if f is None or f.project_id != project_id:
        return None
    return f


def list_findings(
    session: Session,
    project_id: int,
    *,
    status: str | None = None,
    source: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, Any]]:
    """List findings for a project, newest-seen first.

    ``status``: open | resolved | dismissed | promoted (RFC 22 §3.2).
    ``source``: ai_review | zairgrush | routine.
    """
    stmt = (
        select(FindingModel)
        .where(FindingModel.project_id == project_id)
        .order_by(FindingModel.last_seen_at.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(FindingModel.status == status)
    if source is not None:
        stmt = stmt.where(FindingModel.source == source)
    return [finding_to_dict(f) for f in session.execute(stmt).scalars()]


def get_finding(
    session: Session,
    project_id: int,
    finding_uid: str,
) -> dict[str, Any] | None:
    """Return one finding by its stable ``finding_uid``, or None."""
    f = _get_model(session, project_id, finding_uid)
    return finding_to_dict(f) if f is not None else None


def dismiss_finding(
    session: Session,
    *,
    project_id: int,
    finding_uid: str,
    author: str = "mcp",
    reason: str | None = None,
) -> dict[str, Any]:
    """Mark a finding ``dismissed`` (operator triage: not worth a task).

    Emits a ``finding.dismissed`` activity event in the same transaction
    (proposal 09). Dismissing an already-dismissed finding is a no-op and
    does not emit a second event.
    """
    f = _get_model(session, project_id, finding_uid)
    if f is None:
        raise ValueError(f"finding '{finding_uid}' not found")
    if f.status == FINDING_STATUS_DISMISSED:
        return finding_to_dict(f)

    f.status = FINDING_STATUS_DISMISSED
    session.flush()
    activity_service.emit(
        session,
        project_id,
        "finding.dismissed",
        actor_kind=actor_kind_for_author(author),
        actor_id=author,
        scope_kind="finding",
        scope_id=f.finding_uid,
        payload={"finding_id": f.row_id, "source": f.source, "reason": reason},
        summary=f"Finding {f.finding_uid} dismissed by {author}",
    )
    return finding_to_dict(f)
