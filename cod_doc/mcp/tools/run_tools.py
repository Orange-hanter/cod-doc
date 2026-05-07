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
    count = select(func.count()).select_from(AgentRunModel).where(
        AgentRunModel.project_id == project_id
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


def get_run_with_mutations(
    session: Session, run_id: str
) -> dict[str, Any] | None:
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

    @mcp.tool(name="run.list")
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

    @mcp.tool(name="run.get")
    def run_get(project: str, run_id: str) -> dict[str, Any] | None:
        """Get one run with its linked mutations (revisions + audit_log).

        Returns ``None`` when the run_id is unknown for this project.
        """
        from cod_doc.infra.db import transactional

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            return get_run_with_mutations(session, run_id)
