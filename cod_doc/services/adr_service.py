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
- ``render_markdown(...)`` — project one ADR through the default Jinja
  template into a markdown string (for export / git-visibility).
- ``export_to_disk(...)`` — write all ADRs of a project to
  ``<project_root>/docs/adr/ADR-NNN.md`` (idempotent: same content =>
  same file, no spurious diffs).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.models import (
    ADRDiagramModel,
    ADRModel,
    ADRSupersedeModel,
    ADRTaskModel,
    ProjectModel,
)
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields})


class ADRNotFoundError(LookupError):
    pass


class ADRAlreadyExistsError(ValueError):
    pass


class ADRImmutableError(ValueError):
    """Raised when ``update()`` is called on a non-mutable ADR.

    ADR mutability rules (vision §4):
    - PROPOSED — fully mutable.
    - ACCEPTED — only ``supersede()`` and ``deprecate()`` may change state;
      title / body fields are frozen. ``add_diagram`` is still allowed.
    - SUPERSEDED / DEPRECATED / REJECTED — terminal; nothing changes via
      ``update()``.
    """


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
    rows = session.execute(
        select(ADRModel.adr_id).where(ADRModel.project_id == project_id)
    ).scalars().all()
    max_n = 0
    for aid in rows:
        m = _ADR_ID_RE.match(aid or "")
        if m:
            max_n = max(max_n, int(aid.split("-")[1]))
    return f"ADR-{max_n + 1:03d}"


def _require(session: Session, project_id: int, adr_id: str) -> ADRModel:
    row = session.execute(
        select(ADRModel).where(
            ADRModel.project_id == project_id, ADRModel.adr_id == adr_id,
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
                ADRModel.project_id == project_id, ADRModel.adr_id == adr_id,
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
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.ADR,
        entity_id=row.row_id,
        author=author,
        diff=_diff("create", adr_id=adr_id, status=status, title=title),
        reason="create",
    )
    return row


def get(session: Session, project_id: int, adr_id: str) -> ADRModel | None:
    row = session.execute(
        select(ADRModel).where(
            ADRModel.project_id == project_id, ADRModel.adr_id == adr_id,
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


_TERMINAL_STATUSES = {"superseded", "deprecated", "rejected"}


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
    author: str = "human",
    reason: str | None = None,
) -> ADRModel:
    """Patch fields on an ADR. Mutability is gated by current status.

    - ``PROPOSED``: any field may change, including ``status`` (accept it
      via ``status="accepted"``, reject it via ``status="rejected"``).
    - ``ACCEPTED``: ``ADRImmutableError`` unless the caller is a no-op.
      Use :func:`supersede` to transition to ``SUPERSEDED``, or
      :func:`deprecate` to transition to ``DEPRECATED``.
    - Terminal (``SUPERSEDED``/``DEPRECATED``/``REJECTED``): any change
      raises ``ADRImmutableError``.
    """
    row = _require(session, project_id, adr_id)
    changed: dict[str, Any] = {}
    # Tentatively collect every requested change (no-op fields are dropped).
    if status is not None and status != row.status:
        if status not in _LEGAL_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        changed["status"] = {"old": row.status, "new": status}
    if title is not None and title != row.title:
        changed["title"] = {"old": row.title, "new": title}
    if decided_at is not None and decided_at != row.decided_at:
        changed["decided_at"] = {
            "old": row.decided_at.isoformat() if row.decided_at else None,
            "new": decided_at.isoformat(),
        }
    if context is not None and context != row.context:
        changed["context"] = {"changed": True}
    if decision is not None and decision != row.decision:
        changed["decision"] = {"changed": True}
    if alternatives is not None and alternatives != row.alternatives:
        changed["alternatives"] = {"changed": True}
    if consequences is not None and consequences != row.consequences:
        changed["consequences"] = {"changed": True}

    if not changed:
        return row

    # Immutability gate: terminal states reject everything; ACCEPTED rejects
    # body/title/decided_at and any status change (use supersede/deprecate).
    if row.status in _TERMINAL_STATUSES:
        raise ADRImmutableError(
            f"ADR {adr_id} is in terminal status {row.status!r}; cannot update"
        )
    if row.status == "accepted":
        body_fields = set(changed) - {"status"}
        # An ACCEPTED ADR may not change any body/title field via update().
        if body_fields:
            raise ADRImmutableError(
                f"ADR {adr_id} is ACCEPTED; body/title fields are frozen "
                f"(attempted to change: {sorted(body_fields)}). "
                f"Create a superseding ADR to amend the decision."
            )
        # Status changes from ACCEPTED must go through supersede()/deprecate(),
        # not through update().
        if "status" in changed:
            raise ADRImmutableError(
                f"ADR {adr_id} is ACCEPTED; use supersede() or deprecate() "
                f"to leave this state (attempted status change to "
                f"{changed['status']['new']!r})."
            )

    # All gates passed — apply changes.
    if "status" in changed:
        row.status = changed["status"]["new"]
    if "title" in changed:
        row.title = changed["title"]["new"]
    if "decided_at" in changed:
        row.decided_at = decided_at
    if "context" in changed:
        row.context = context
    if "decision" in changed:
        row.decision = decision
    if "alternatives" in changed:
        row.alternatives = alternatives
    if "consequences" in changed:
        row.consequences = consequences
    row.last_updated = datetime.now(UTC)
    session.flush()
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.ADR,
        entity_id=row.row_id,
        author=author,
        diff=_diff("update", adr_id=adr_id, **changed),
        reason=reason or "update",
    )
    return row


def deprecate(
    session: Session,
    *,
    project_id: int,
    adr_id: str,
    reason: str | None = None,
    author: str = "human",
) -> ADRModel:
    """Transition ``ACCEPTED`` (or ``PROPOSED``) → ``DEPRECATED``.

    The only legitimate way to retire an ADR without replacing it.
    Re-applying to an already-DEPRECATED ADR is idempotent (no-op).
    Terminal statuses other than DEPRECATED raise ``ADRImmutableError``.
    """
    row = _require(session, project_id, adr_id)
    if row.status == "deprecated":
        return row
    if row.status in _TERMINAL_STATUSES:
        raise ADRImmutableError(
            f"ADR {adr_id} is in terminal status {row.status!r}; cannot deprecate"
        )
    old_status = row.status
    row.status = "deprecated"
    row.last_updated = datetime.now(UTC)
    session.flush()
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.ADR,
        entity_id=row.row_id,
        author=author,
        diff=_diff("deprecate", adr_id=adr_id, old=old_status, reason=reason),
        reason=reason or "deprecate",
    )
    return row


def add_diagram(
    session: Session,
    *,
    project_id: int,
    adr_id: str,
    mermaid: str,
    title: str | None = None,
    position: int | None = None,
    author: str = "human",
) -> ADRDiagramModel:
    adr = _require(session, project_id, adr_id)
    if adr.status in _TERMINAL_STATUSES:
        raise ADRImmutableError(
            f"ADR {adr_id} is in terminal status {adr.status!r}; "
            f"diagrams can only be added to PROPOSED or ACCEPTED ADRs"
        )
    if position is None:
        # Append at end.
        max_pos = session.execute(
            select(func.coalesce(func.max(ADRDiagramModel.position), -1))
            .where(ADRDiagramModel.adr_id == adr.row_id)
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
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.ADR,
        entity_id=adr.row_id,
        author=author,
        diff=_diff(
            "add_diagram",
            adr_id=adr_id,
            position=position,
            title=title,
            diagram_id=row.row_id,
        ),
        reason="add_diagram",
    )
    return row


def supersede(
    session: Session,
    *,
    project_id: int,
    superseding_adr_id: str,
    superseded_adr_id: str,
    reason: str | None = None,
    author: str = "human",
) -> ADRSupersedeModel:
    """Mark ``superseded_adr_id`` as superseded BY ``superseding_adr_id``.

    Two effects in one transaction:
    1. New row in ``adr_supersedes`` table (the DAG edge).
    2. Old ADR's status flipped to ``superseded`` (idempotent).

    Cycle detection: rejects an edge that would close a cycle in the
    supersede DAG (e.g. ``A → B`` exists, refuse ``B → A``).
    """
    if superseding_adr_id == superseded_adr_id:
        raise ValueError("an ADR cannot supersede itself")
    new = _require(session, project_id, superseding_adr_id)
    old = _require(session, project_id, superseded_adr_id)
    # Cycle check: walk supersedes from `old` (downstream); if we reach `new`,
    # the new edge would close a cycle.
    if _has_path(session, start_id=old.row_id, target_id=new.row_id):
        raise ValueError(
            f"supersede {superseding_adr_id} → {superseded_adr_id} "
            f"would create a cycle in the supersede DAG"
        )
    # Idempotency: don't create duplicate edge.
    existing = session.execute(
        select(ADRSupersedeModel).where(
            ADRSupersedeModel.superseding_id == new.row_id,
            ADRSupersedeModel.superseded_id == old.row_id,
        )
    ).scalar_one_or_none()
    is_new_edge = existing is None
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
    flipped = False
    if old.status != "superseded":
        old.status = "superseded"
        old.last_updated = datetime.now(UTC)
        flipped = True
    session.flush()
    if is_new_edge:
        # Write a revision against the OLD ADR (the affected one).
        rev.write(
            session,
            project_id=project_id,
            entity_kind=EntityKind.ADR,
            entity_id=old.row_id,
            author=author,
            diff=_diff(
                "supersede",
                old=superseded_adr_id,
                new=superseding_adr_id,
                reason=reason,
                status_flipped=flipped,
            ),
            reason=reason or "supersede",
        )
    return edge


def _has_path(session: Session, *, start_id: int, target_id: int) -> bool:
    """DFS over `adr_supersedes` from ``start_id``; True iff ``target_id`` is reachable."""
    if start_id == target_id:
        return True
    seen: set[int] = set()
    stack: list[int] = [start_id]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        if node == target_id:
            return True
        next_ids = session.execute(
            select(ADRSupersedeModel.superseded_id).where(
                ADRSupersedeModel.superseding_id == node
            )
        ).scalars().all()
        for nid in next_ids:
            if nid not in seen:
                stack.append(nid)
    return False


def link_task(
    session: Session,
    *,
    project_id: int,
    adr_id: str,
    task_id: str,
    relation: str = "implements",
    author: str = "human",
) -> ADRTaskModel:
    if relation not in _LEGAL_RELATIONS:
        raise ValueError(f"invalid relation {relation!r}; expected one of {sorted(_LEGAL_RELATIONS)}")
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
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.ADR,
        entity_id=adr.row_id,
        author=author,
        diff=_diff("link_task", adr_id=adr_id, task_id=task_id, relation=relation),
        reason="link_task",
    )
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
        "nodes": [
            {"adr_id": n.adr_id, "title": n.title, "status": n.status}
            for n in nodes
        ],
        "edges": [
            {"from": e.new_id, "to": e.old_id, "reason": e.reason}
            for e in edge_rows
        ],
    }


# ----------------------------------------------------------------- #
# Helpers for MCP dict-ification                                     #
# ----------------------------------------------------------------- #


def adr_to_dict(
    session: Session, adr: ADRModel, *, include_diagrams: bool = True, include_links: bool = True,
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
        diagrams = session.execute(
            select(ADRDiagramModel)
            .where(ADRDiagramModel.adr_id == adr.row_id)
            .order_by(ADRDiagramModel.position)
        ).scalars().all()
        out["diagrams"] = [
            {"position": d.position, "title": d.title, "mermaid": d.mermaid}
            for d in diagrams
        ]
    if include_links:
        links = session.execute(
            select(ADRTaskModel)
            .where(ADRTaskModel.adr_row_id == adr.row_id)
        ).scalars().all()
        out["task_links"] = [
            {"task_id": link.task_id, "relation": link.relation}
            for link in links
        ]
    return out


# ----------------------------------------------------------------- #
# Markdown projection (P2-1)                                          #
# ----------------------------------------------------------------- #


_STATUS_ICON = {
    "proposed": "✏️",
    "accepted": "✅",
    "superseded": "🔁",
    "deprecated": "⚠️",
    "rejected": "❌",
}

# Locate the bundled default template relative to the package root.
_PKG_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES_DIR = _PKG_ROOT / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
)


def render_markdown(
    session: Session,
    *,
    project_id: int,
    adr_id: str,
    template: str = "adr_default.md.j2",
) -> str:
    """Render one ADR through the Jinja template into markdown.

    Pure function: no I/O beyond reading the template. The DB session
    is used only to fetch the ADR + diagrams + task-links.
    """
    row = _require(session, project_id, adr_id)
    payload = adr_to_dict(session, row)
    payload["status_icon"] = _STATUS_ICON.get(payload["status"], "•")
    # Resolve the supersedes chain: list of ADR-NNN ids that THIS one replaces.
    superseded_rows = session.execute(
        select(ADRSupersedeModel, ADRModel.adr_id)
        .join(ADRModel, ADRModel.row_id == ADRSupersedeModel.superseded_id)
        .where(ADRSupersedeModel.superseding_id == row.row_id)
    ).all()
    payload["supersedes"] = [
        {"adr_id": adr_id_str, "reason": edge.reason}
        for edge, adr_id_str in superseded_rows
    ]
    payload["adr_id"] = row.adr_id
    tmpl = _jinja_env.get_template(template)
    return tmpl.render(**payload)


def export_to_disk(
    session: Session,
    *,
    project_id: int,
    out_dir: Path | str | None = None,
) -> list[Path]:
    """Project every ADR of ``project_id`` into ``<out_dir>/ADR-NNN.md``.

    ``out_dir`` defaults to ``<project.root_path>/docs/adr``. Idempotent:
    files whose content matches the new rendering are skipped, so re-running
    produces no spurious diffs. Returns the list of paths that were written
    (new or changed). Caller is responsible for any commit / cleanup.
    """
    proj = session.execute(
        select(ProjectModel).where(ProjectModel.row_id == project_id)
    ).scalar_one_or_none()
    if proj is None:
        raise LookupError(f"project not found: id={project_id}")
    if out_dir is None:
        out_dir = Path(proj.root_path) / "docs" / "adr"
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for adr in list_for_project(session, project_id):
        rendered = render_markdown(
            session, project_id=project_id, adr_id=adr.adr_id,
        )
        target = out_path / f"{adr.adr_id}.md"
        if target.exists() and target.read_text(encoding="utf-8") == rendered:
            continue
        target.write_text(rendered, encoding="utf-8")
        written.append(target)
    return written
