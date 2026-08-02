"""ADR-002: AdrService — CRUD + supersede DAG + task linking.

Public entry points (consumed by MCP wrappers in ``cod_doc.mcp.tools.adr_tools``
and CLI in ``cod_doc.cli.adr``):

- ``create(...)`` — new ADR with auto-allocated id (next ADR-NNN).
- ``get(project_id, adr_id)`` — single ADR with diagrams + task links.
- ``list_for_project(project_id, status?)`` — filtered listing.
- ``update(adr_id, **changes)`` — status / title / context / decision /
  alternatives / consequences / decided_at.
- ``add_diagram(adr_id, title?, mermaid, position?)``.
- ``supersede(new_adr_id, old_adr_id, reason?)`` — create edge AND
  mark old as ``superseded`` in one transaction.
- ``link_task(adr_id, task_id, relation='implements')``.
- ``graph(project_id)`` — full supersede DAG + status-by-node payload.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from cod_doc.infra.models import (
    ADRDiagramModel,
    ADRModel,
    ADRSupersedeModel,
    ADRTaskModel,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ADRNotFoundError(LookupError):
    pass


class ADRAlreadyExistsError(ValueError):
    pass


# ----------------------------------------------------------------- #
# Helpers                                                            #
# ----------------------------------------------------------------- #


_ADR_ID_RE = re.compile(r"^ADR-\d{3}$")
_LEGAL_STATUSES = {"proposed", "accepted", "superseded", "deprecated", "rejected"}
_LEGAL_RELATIONS = {"implements", "invalidates", "discovers", "relates"}


def _validate_adr_id(adr_id: str) -> None:
    if not _ADR_ID_RE.match(adr_id):
        raise ValueError(f"invalid adr_id {adr_id!r}; expected format 'ADR-NNN' (3 digits)")


def _next_adr_id(session: Session, project_id: int) -> str:
    """Assign ``ADR-NNN`` as max-existing-N + 1, zero-padded to 3."""
    rows = (
        session.execute(select(ADRModel.adr_id).where(ADRModel.project_id == project_id))
        .scalars()
        .all()
    )
    max_n = 0
    for aid in rows:
        m = _ADR_ID_RE.match(aid or "")
        if m:
            max_n = max(max_n, int(aid.split("-")[1]))
    return f"ADR-{max_n + 1:03d}"


def _require(session: Session, project_id: int, adr_id: str) -> ADRModel:
    row = session.execute(
        select(ADRModel).where(
            ADRModel.project_id == project_id,
            ADRModel.adr_id == adr_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise ADRNotFoundError(f"ADR not found: {adr_id} (project_id={project_id})")
    return row


# ----------------------------------------------------------------- #
# CRUD                                                                #
# ----------------------------------------------------------------- #


def create(
    session: Session,
    *,
    project_id: int,
    title: str,
    status: str = "proposed",
    decided_at: date | None = None,
    context: str | None = None,
    decision: str | None = None,
    alternatives: str | None = None,
    consequences: str | None = None,
    author: str = "human",
    adr_id: str | None = None,
) -> ADRModel:
    if status not in _LEGAL_STATUSES:
        raise ValueError(f"invalid status {status!r}; expected one of {sorted(_LEGAL_STATUSES)}")
    if adr_id is not None:
        _validate_adr_id(adr_id)
        existing = session.execute(
            select(ADRModel).where(
                ADRModel.project_id == project_id,
                ADRModel.adr_id == adr_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ADRAlreadyExistsError(f"ADR {adr_id} already exists in project")
    else:
        adr_id = _next_adr_id(session, project_id)

    now = datetime.now(UTC)
    row = ADRModel(
        project_id=project_id,
        adr_id=adr_id,
        title=title,
        status=status,
        decided_at=decided_at,
        context=context,
        decision=decision,
        alternatives=alternatives,
        consequences=consequences,
        author=author,
        created=now,
        last_updated=now,
    )
    session.add(row)
    session.flush()
    return row


def get(session: Session, project_id: int, adr_id: str) -> ADRModel | None:
    row = session.execute(
        select(ADRModel).where(
            ADRModel.project_id == project_id,
            ADRModel.adr_id == adr_id,
        )
    ).scalar_one_or_none()
    return row


def list_for_project(
    session: Session,
    project_id: int,
    *,
    status: str | None = None,
) -> list[ADRModel]:
    stmt = select(ADRModel).where(ADRModel.project_id == project_id)
    if status is not None:
        if status not in _LEGAL_STATUSES:
            raise ValueError(f"invalid status filter {status!r}")
        stmt = stmt.where(ADRModel.status == status)
    return list(session.execute(stmt.order_by(ADRModel.adr_id)).scalars())


def update(
    session: Session,
    *,
    project_id: int,
    adr_id: str,
    title: str | None = None,
    status: str | None = None,
    decided_at: date | None = None,
    context: str | None = None,
    decision: str | None = None,
    alternatives: str | None = None,
    consequences: str | None = None,
) -> ADRModel:
    row = _require(session, project_id, adr_id)
    if status is not None:
        if status not in _LEGAL_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        row.status = status
    if title is not None:
        row.title = title
    if decided_at is not None:
        row.decided_at = decided_at
    if context is not None:
        row.context = context
    if decision is not None:
        row.decision = decision
    if alternatives is not None:
        row.alternatives = alternatives
    if consequences is not None:
        row.consequences = consequences
    row.last_updated = datetime.now(UTC)
    session.flush()
    return row


def add_diagram(
    session: Session,
    *,
    project_id: int,
    adr_id: str,
    mermaid: str,
    title: str | None = None,
    position: int | None = None,
) -> ADRDiagramModel:
    adr = _require(session, project_id, adr_id)
    if position is None:
        # Append at end.
        max_pos = session.execute(
            select(func.coalesce(func.max(ADRDiagramModel.position), -1)).where(
                ADRDiagramModel.adr_id == adr.row_id
            )
        ).scalar_one()
        position = int(max_pos) + 1
    row = ADRDiagramModel(
        adr_id=adr.row_id,
        position=position,
        title=title,
        mermaid=mermaid,
    )
    session.add(row)
    session.flush()
    return row


def supersede(
    session: Session,
    *,
    project_id: int,
    superseding_adr_id: str,
    superseded_adr_id: str,
    reason: str | None = None,
) -> ADRSupersedeModel:
    """Mark ``superseded_adr_id`` as superseded BY ``superseding_adr_id``.

    Two effects in one transaction:
    1. New row in ``adr_supersedes`` table (the DAG edge).
    2. Old ADR's status flipped to ``superseded`` (idempotent).
    """
    if superseding_adr_id == superseded_adr_id:
        raise ValueError("an ADR cannot supersede itself")
    new = _require(session, project_id, superseding_adr_id)
    old = _require(session, project_id, superseded_adr_id)
    # Idempotency: don't create duplicate edge.
    existing = session.execute(
        select(ADRSupersedeModel).where(
            ADRSupersedeModel.superseding_id == new.row_id,
            ADRSupersedeModel.superseded_id == old.row_id,
        )
    ).scalar_one_or_none()
    if existing is None:
        edge = ADRSupersedeModel(
            superseding_id=new.row_id,
            superseded_id=old.row_id,
            reason=reason,
            at=datetime.now(UTC),
        )
        session.add(edge)
    else:
        edge = existing
        if reason and not edge.reason:
            edge.reason = reason
    # Flip old status.
    if old.status != "superseded":
        old.status = "superseded"
        old.last_updated = datetime.now(UTC)
    session.flush()
    return edge


def link_task(
    session: Session,
    *,
    project_id: int,
    adr_id: str,
    task_id: str,
    relation: str = "implements",
) -> ADRTaskModel:
    if relation not in _LEGAL_RELATIONS:
        raise ValueError(
            f"invalid relation {relation!r}; expected one of {sorted(_LEGAL_RELATIONS)}"
        )
    adr = _require(session, project_id, adr_id)
    # Idempotency.
    existing = session.execute(
        select(ADRTaskModel).where(
            ADRTaskModel.adr_row_id == adr.row_id,
            ADRTaskModel.task_id == task_id,
            ADRTaskModel.relation == relation,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = ADRTaskModel(adr_row_id=adr.row_id, task_id=task_id, relation=relation)
    session.add(row)
    session.flush()
    return row


def graph(session: Session, project_id: int) -> dict[str, Any]:
    """Return the supersede DAG for ``project_id``:

    {
      "nodes": [{adr_id, title, status}, ...],
      "edges": [{from: adr_id, to: adr_id, reason}, ...]
    }
    """
    nodes = list(
        session.execute(
            select(ADRModel.adr_id, ADRModel.title, ADRModel.status)
            .where(ADRModel.project_id == project_id)
            .order_by(ADRModel.adr_id)
        ).all()
    )
    # Join edges to adr_id strings.
    new_alias = ADRModel.__table__.alias("new_adr")
    old_alias = ADRModel.__table__.alias("old_adr")
    edge_rows = session.execute(
        select(
            new_alias.c.adr_id.label("new_id"),
            old_alias.c.adr_id.label("old_id"),
            ADRSupersedeModel.reason,
        )
        .select_from(ADRSupersedeModel)
        .join(new_alias, new_alias.c.row_id == ADRSupersedeModel.superseding_id)
        .join(old_alias, old_alias.c.row_id == ADRSupersedeModel.superseded_id)
        .where(new_alias.c.project_id == project_id)
    ).all()
    return {
        "nodes": [{"adr_id": n.adr_id, "title": n.title, "status": n.status} for n in nodes],
        "edges": [{"from": e.new_id, "to": e.old_id, "reason": e.reason} for e in edge_rows],
    }


# ----------------------------------------------------------------- #
# Helpers for MCP dict-ification                                     #
# ----------------------------------------------------------------- #


def adr_to_dict(
    session: Session,
    adr: ADRModel,
    *,
    include_diagrams: bool = True,
    include_links: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "adr_id": adr.adr_id,
        "title": adr.title,
        "status": adr.status,
        "decided_at": adr.decided_at.isoformat() if adr.decided_at else None,
        "context": adr.context,
        "decision": adr.decision,
        "alternatives": adr.alternatives,
        "consequences": adr.consequences,
        "author": adr.author,
        "created": adr.created.isoformat() if adr.created else None,
        "last_updated": adr.last_updated.isoformat() if adr.last_updated else None,
    }
    if include_diagrams:
        diagrams = (
            session.execute(
                select(ADRDiagramModel)
                .where(ADRDiagramModel.adr_id == adr.row_id)
                .order_by(ADRDiagramModel.position)
            )
            .scalars()
            .all()
        )
        out["diagrams"] = [
            {"position": d.position, "title": d.title, "mermaid": d.mermaid} for d in diagrams
        ]
    if include_links:
        links = (
            session.execute(select(ADRTaskModel).where(ADRTaskModel.adr_row_id == adr.row_id))
            .scalars()
            .all()
        )
        out["task_links"] = [{"task_id": link.task_id, "relation": link.relation} for link in links]
    return out
