"""ADR-002: 7 MCP tools for Architecture Decision Records.

- ``adr_create`` — new ADR (auto-allocates ADR-NNN if not given).
- ``adr_get`` — single ADR with diagrams + task links.
- ``adr_list`` — listing with optional status filter.
- ``adr_update`` — fields / status.
- ``adr_add_diagram`` — attach a Mermaid diagram.
- ``adr_supersede`` — DAG edge + auto-flip old status.
- ``adr_link_task`` — link ADR ↔ task.
- ``adr_graph`` — full supersede DAG for visualisation.

Standard/full-profile surface. Agent profile reaches ADRs through
``agent_get(what='adr_full', ref=<adr_id>)`` once that branch is wired
(future work).
"""

from __future__ import annotations

from datetime import date as _date
from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _parse_date(s: str | None) -> _date | None:
    if not s:
        return None
    return _date.fromisoformat(s)


def register(mcp: FastMCP) -> None:
    """Register adr.* tools on the given FastMCP instance."""

    @mcp.tool(name="adr_create")
    def adr_create(
        project: str,
        title: str,
        status: str = "proposed",
        decided_at: str | None = None,
        context: str | None = None,
        decision: str | None = None,
        alternatives: str | None = None,
        consequences: str | None = None,
        adr_id: str | None = None,
        author: str = "human",
    ) -> dict[str, Any]:
        """Create a new ADR. ID auto-allocated as ADR-NNN if not provided.

        status: proposed | accepted | superseded | deprecated | rejected.
        decided_at: ISO date string (YYYY-MM-DD), optional.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            row = adr_service.create(
                session, project_id=project_id, title=title, status=status,
                decided_at=_parse_date(decided_at), context=context,
                decision=decision, alternatives=alternatives,
                consequences=consequences, adr_id=adr_id, author=author,
            )
            return adr_service.adr_to_dict(session, row)

    @mcp.tool(name="adr_get")
    def adr_get(project: str, adr_id: str) -> dict[str, Any]:
        """Return an ADR with its diagrams and task links.

        Miss returns ``{found: false, requested_adr_id, hint, related_tools}``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            row = adr_service.get(session, project_id, adr_id)
            if row is None:
                return {
                    "found": False,
                    "requested_adr_id": adr_id,
                    "hint": (
                        f"ADR {adr_id!r} not found in project {project!r}. "
                        "Try adr_list to browse, or adr_create to add one."
                    ),
                    "related_tools": ["adr_list", "adr_create"],
                }
            out = adr_service.adr_to_dict(session, row)
            out["found"] = True
            return out

    @mcp.tool(name="adr_list")
    def adr_list(project: str, status: str | None = None) -> list[dict[str, Any]]:
        """List ADRs in a project, optionally filtered by status.

        Returns a list of compact rows (no diagrams/links — use adr_get for those).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            rows = adr_service.list_for_project(session, project_id, status=status)
            return [
                {
                    "adr_id": r.adr_id, "title": r.title, "status": r.status,
                    "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                    "author": r.author,
                }
                for r in rows
            ]

    @mcp.tool(name="adr_update")
    def adr_update(
        project: str,
        adr_id: str,
        title: str | None = None,
        status: str | None = None,
        decided_at: str | None = None,
        context: str | None = None,
        decision: str | None = None,
        alternatives: str | None = None,
        consequences: str | None = None,
    ) -> dict[str, Any]:
        """Patch ADR fields. Only supplied (non-None) fields change."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import ADRNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                row = adr_service.update(
                    session, project_id=project_id, adr_id=adr_id,
                    title=title, status=status,
                    decided_at=_parse_date(decided_at), context=context,
                    decision=decision, alternatives=alternatives,
                    consequences=consequences,
                )
                return adr_service.adr_to_dict(session, row)
        except ADRNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_add_diagram")
    def adr_add_diagram(
        project: str,
        adr_id: str,
        mermaid: str,
        title: str | None = None,
        position: int | None = None,
    ) -> dict[str, Any]:
        """Attach a Mermaid diagram to an ADR. Auto-appends if position omitted."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import ADRNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                d = adr_service.add_diagram(
                    session, project_id=project_id, adr_id=adr_id,
                    mermaid=mermaid, title=title, position=position,
                )
                return {
                    "adr_id": adr_id, "position": d.position,
                    "title": d.title, "diagram_id": d.row_id,
                }
        except ADRNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_supersede")
    def adr_supersede(
        project: str,
        superseding_adr_id: str,
        superseded_adr_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Mark ``superseded_adr_id`` as replaced by ``superseding_adr_id``.

        Creates the DAG edge AND auto-flips the old ADR's status to
        ``superseded`` in one transaction. Idempotent: re-running with
        the same pair returns the existing edge.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import ADRNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                edge = adr_service.supersede(
                    session, project_id=project_id,
                    superseding_adr_id=superseding_adr_id,
                    superseded_adr_id=superseded_adr_id,
                    reason=reason,
                )
                return {
                    "superseding": superseding_adr_id,
                    "superseded": superseded_adr_id,
                    "reason": edge.reason,
                    "at": edge.at.isoformat() if edge.at else None,
                }
        except ADRNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_link_task")
    def adr_link_task(
        project: str,
        adr_id: str,
        task_id: str,
        relation: str = "implements",
    ) -> dict[str, Any]:
        """Link a task to an ADR. relation: implements | invalidates | discovers | relates."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import ADRNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                link = adr_service.link_task(
                    session, project_id=project_id, adr_id=adr_id,
                    task_id=task_id, relation=relation,
                )
                return {
                    "adr_id": adr_id, "task_id": link.task_id,
                    "relation": link.relation,
                }
        except ADRNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_graph")
    def adr_graph(project: str) -> dict[str, Any]:
        """Return the full supersede DAG: ``{nodes: [...], edges: [...]}``.

        Each node carries adr_id + title + status; each edge carries
        from/to (canonical ADR-NNN ids) + reason. Suitable for direct
        Mermaid rendering.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return adr_service.graph(session, project_id)
