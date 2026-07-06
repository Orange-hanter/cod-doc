"""MCP tools: run.* — agent run-id audit trail (PCA-032, proposal 04).

Exposes the `agent_run` table + run-linked mutations via MCP. Once
``Orchestrator.run_task`` enters ``run_scope`` (PCA-031), every revision
written downstream carries the run_id; these tools let agents (and
humans) ask "what happened on run X" and "what runs has the system seen
recently".

The query helpers (``list_runs_for_project``, ``get_run_with_mutations``)
are module-level + session-typed so unit tests can call them directly,
without spinning up a FastMCP instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from cod_doc.infra.models import AgentRunModel, AuditLogModel, RevisionModel
from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.orm import Session


# --------------------------------------------------------------------------- #
# Query helpers (testable without FastMCP)                                    #
# --------------------------------------------------------------------------- #


def list_runs_for_project(
    session: Session,
    project_id: int,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Newest-first runs of a project, optionally filtered by status."""
    base = select(AgentRunModel).where(AgentRunModel.project_id == project_id)
    count = (
        select(func.count())
        .select_from(AgentRunModel)
        .where(AgentRunModel.project_id == project_id)
    )
    if status is not None:
        base = base.where(AgentRunModel.status == status)
        count = count.where(AgentRunModel.status == status)
    total = int(session.execute(count).scalar_one() or 0)
    rows = session.execute(
        base.order_by(AgentRunModel.started_at.desc(), AgentRunModel.row_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars()
    return {
        "items": [_run_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def plan_run_revert(session: Session, run_id: str) -> dict[str, Any] | None:
    """PCA-033: enumerate inverse operations for a run, with conflict detection.

    Read-only. Returns ``None`` if the run_id is unknown.

    For each revision stamped with ``run_id``, walks the entity's revision
    chain and reports:
    - ``op``: a label of the reverse step (revision_id of the candidate undo).
    - ``conflicts``: revisions written *after* this run on the same entity —
      those mean a later run already touched the artifact, and a real revert
      would have to be a 3-way merge (out of scope here). Empty list = clean
      revert candidate.

    The actual destructive revert (`revision_revert`) is wired entity-by-
    entity in COD-022 and is invoked by the operator when the dry-run
    output looks acceptable. PCA-033 only assembles the proposal.
    """
    from sqlalchemy import select

    run = session.execute(
        select(AgentRunModel).where(AgentRunModel.run_id == run_id)
    ).scalar_one_or_none()
    if run is None:
        return None

    revs = list(
        session.execute(
            select(RevisionModel)
            .where(RevisionModel.run_id == run_id)
            .order_by(RevisionModel.at.asc(), RevisionModel.row_id.asc())
        ).scalars()
    )

    operations: list[dict[str, Any]] = []
    for r in revs:
        # Conflict = a revision on the same entity strictly newer than this
        # one and stamped with a *different* run_id (or None — human edit).
        conflicts_q = (
            select(RevisionModel.revision_id, RevisionModel.run_id, RevisionModel.author)
            .where(
                RevisionModel.entity_kind == r.entity_kind,
                RevisionModel.entity_id == r.entity_id,
                (RevisionModel.at > r.at)
                | ((RevisionModel.at == r.at) & (RevisionModel.row_id > r.row_id)),
            )
            .order_by(RevisionModel.at.asc(), RevisionModel.row_id.asc())
        )
        conflicts = [
            {"revision_id": rev_id, "run_id": rid, "author": author}
            for rev_id, rid, author in session.execute(conflicts_q)
            if rid != run_id  # mutations from the same run are part of the rollback, not conflicts
        ]
        operations.append(
            {
                "revision_id": r.revision_id,
                "entity_kind": r.entity_kind,
                "entity_id": r.entity_id,
                "author": r.author,
                "at": r.at.isoformat() if r.at else None,
                "conflicts": conflicts,
            }
        )

    return {
        "run_id": run_id,
        "status": run.status,
        "operations": operations,
        "total_operations": len(operations),
        "total_conflicts": sum(1 for op in operations if op["conflicts"]),
        "dry_run": True,
    }


def get_run_with_mutations(session: Session, run_id: str) -> dict[str, Any] | None:
    """Single run + revisions + audit_log entries stamped with the same run_id."""
    run = session.execute(
        select(AgentRunModel).where(AgentRunModel.run_id == run_id)
    ).scalar_one_or_none()
    if run is None:
        return None

    revs = list(
        session.execute(
            select(RevisionModel)
            .where(RevisionModel.run_id == run_id)
            .order_by(RevisionModel.at.desc(), RevisionModel.row_id.desc())
        ).scalars()
    )
    audit = list(
        session.execute(
            select(AuditLogModel)
            .where(AuditLogModel.run_id == run_id)
            .order_by(AuditLogModel.at.desc(), AuditLogModel.row_id.desc())
        ).scalars()
    )

    return {
        **_run_to_dict(run),
        "mutations": {
            "revisions": [
                {
                    "revision_id": r.revision_id,
                    "entity_kind": r.entity_kind,
                    "entity_id": r.entity_id,
                    "author": r.author,
                    "at": r.at.isoformat() if r.at else None,
                }
                for r in revs
            ],
            "audit_log": [
                {
                    "row_id": a.row_id,
                    "actor": a.actor,
                    "surface": a.surface,
                    "action": a.action,
                    "result": a.result,
                    "at": a.at.isoformat() if a.at else None,
                }
                for a in audit
            ],
        },
    }


def _run_to_dict(run: AgentRunModel) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "project_id": run.project_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "wake_reason": run.wake_reason,
        "triggering_task_id": run.triggering_task_id,
        "triggering_doc_ref": run.triggering_doc_ref,
        "llm_calls": run.llm_calls,
        "llm_tokens_in": run.llm_tokens_in,
        "llm_tokens_out": run.llm_tokens_out,
        "status": run.status,
        "summary": run.summary,
    }


# --------------------------------------------------------------------------- #
# MCP registration                                                             #
# --------------------------------------------------------------------------- #


def register(mcp: FastMCP) -> None:
    """Register run.* tools on the given FastMCP instance."""

    @mcp.tool(name="run_list")
    def run_list(
        project: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List agent runs for a project — newest first, paginated.

        Status filter: running | done | failed | cancelled.
        """
        from cod_doc.infra.db import transactional

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return list_runs_for_project(
                session, project_id, status=status, limit=limit, offset=offset
            )

    @mcp.tool(name="run_get")
    def run_get(project: str, run_id: str) -> dict[str, Any] | None:
        """Get one run with its linked mutations (revisions + audit_log).

        Returns ``None`` when the run_id is unknown for this project.
        """
        from cod_doc.infra.db import transactional

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            return get_run_with_mutations(session, run_id)

    @mcp.tool(name="run_revert")
    def run_revert(
        project: str,
        run_id: str,
        dry_run: bool = True,
    ) -> dict[str, Any] | None:
        """PCA-033: enumerate inverse ops for a run (read-only dry_run).

        ``dry_run=True`` is the only supported mode for now: returns the
        list of revisions that would be reverted plus any conflicts
        (newer revisions on the same entity from other runs / humans).
        Real destructive revert is wired entity-by-entity in COD-022 and
        is invoked by the operator after reviewing the dry-run output.
        ``dry_run=False`` raises ``NotImplementedError`` for safety until
        the wrapper that walks ``revision_revert`` per op lands.
        """
        if not dry_run:
            raise NotImplementedError(
                "run.revert dry_run=False is not yet supported — review the "
                "dry_run output and call revision.revert per row."
            )

        from cod_doc.infra.db import transactional

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            return plan_run_revert(session, run_id)
