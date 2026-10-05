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
                session,
                project_id=project_id,
                title=title,
                status=status,
                decided_at=_parse_date(decided_at),
                context=context,
                decision=decision,
                alternatives=alternatives,
                consequences=consequences,
                adr_id=adr_id,
                author=author,
            )
            return adr_service.adr_to_dict(session, row)

    @mcp.tool(name="adr_get")
    def adr_get(project: str, adr_id: str) -> dict[str, Any]:
        """Return an ADR with its diagrams, task links and ``referenced_by``
        (doc sections, tasks and other ADRs that mention it).

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
            out = adr_service.adr_to_dict(session, row, include_backlinks=True)
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
                    "adr_id": r.adr_id,
                    "title": r.title,
                    "status": r.status,
                    "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                    "author": r.author,
                    "topic": adr_service.topic_name(session, r),
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
        """Patch ADR fields. Only supplied (non-None) fields change.

        Accepting (``status="accepted"``) without ``decided_at`` stamps
        today's date unless the ADR already has one (ARG-002).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import ADRNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                row = adr_service.update(
                    session,
                    project_id=project_id,
                    adr_id=adr_id,
                    title=title,
                    status=status,
                    decided_at=_parse_date(decided_at),
                    context=context,
                    decision=decision,
                    alternatives=alternatives,
                    consequences=consequences,
                    author="agent",
                )
                return adr_service.adr_to_dict(session, row)
        except ADRNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_sync_body")
    def adr_sync_body(
        project: str,
        adr_id: str,
        title: str | None = None,
        decided_at: str | None = None,
        context: str | None = None,
        decision: str | None = None,
        alternatives: str | None = None,
        consequences: str | None = None,
        clear_decided_at: bool = False,
    ) -> dict[str, Any]:
        """Re-sync an ADR body from its markdown projection (ADO-168).

        Use when the markdown is the source and the DB row fell behind it —
        a corrected citation, a section the parser missed. Unlike
        ``adr_update`` this works on ACCEPTED and on terminal
        (SUPERSEDED/DEPRECATED/REJECTED) ADRs: the decision is not being
        amended, only its record catches up.

        ``status`` is deliberately absent. Changing status stays a
        decision-level act — ``adr_update`` (from PROPOSED),
        ``adr_deprecate`` or ``adr_supersede``.

        The audit trail distinguishes the two: revision ``op=sync_body`` and
        activity event ``adr.body_synced``. Supplying nothing that differs
        from the stored row is a no-op — no revision, no event.

        ``clear_decided_at=True`` (ARG-002) removes the decision date —
        ``decided_at=None`` means "leave as is", so clearing needs its own
        flag. Passing both is an error.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import ADRNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                row = adr_service.sync_body(
                    session,
                    project_id=project_id,
                    adr_id=adr_id,
                    title=title,
                    decided_at=_parse_date(decided_at),
                    context=context,
                    decision=decision,
                    alternatives=alternatives,
                    consequences=consequences,
                    author="agent",
                    clear_decided_at=clear_decided_at,
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
                    session,
                    project_id=project_id,
                    adr_id=adr_id,
                    mermaid=mermaid,
                    title=title,
                    position=position,
                    author="agent",
                )
                return {
                    "adr_id": adr_id,
                    "position": d.position,
                    "title": d.title,
                    "diagram_id": d.row_id,
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
                    session,
                    project_id=project_id,
                    superseding_adr_id=superseding_adr_id,
                    superseded_adr_id=superseded_adr_id,
                    reason=reason,
                    author="agent",
                )
                return {
                    "superseding": superseding_adr_id,
                    "superseded": superseded_adr_id,
                    "reason": edge.reason,
                    "at": edge.at.isoformat() if edge.at else None,
                }
        except ADRNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_relate")
    def adr_relate(
        project: str,
        from_adr_id: str,
        to_adr_id: str,
        kind: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Record that ``from_adr_id`` amends or depends on ``to_adr_id`` (ARG-001).

        ``kind``: ``amends`` — both decisions stay in force, the new one
        changes the scope of the old (ADR-010 amends ADR-005);
        ``depends_on`` — ``from`` cannot be accepted before ``to``
        (ADR-017 depends on ADR-016). Unlike ``adr_supersede`` neither
        status changes, and the relation may be recorded from any status of
        ``from``, including ACCEPTED.

        A repeated relation, a self-loop, a cycle within one kind or an
        unknown ``kind`` is an error. To change ``reason``, call
        ``adr_unrelate`` first. Both sides show up in ``adr_get`` under
        ``relations.outgoing`` / ``relations.incoming``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import ADRNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                edge = adr_service.relate(
                    session,
                    project_id=project_id,
                    from_adr_id=from_adr_id,
                    to_adr_id=to_adr_id,
                    kind=kind,
                    reason=reason,
                    author="agent",
                )
                return {
                    "from": from_adr_id,
                    "to": to_adr_id,
                    "kind": edge.kind,
                    "reason": edge.reason,
                    "at": edge.at.isoformat() if edge.at else None,
                }
        except ADRNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_unrelate")
    def adr_unrelate(
        project: str,
        from_adr_id: str,
        to_adr_id: str,
        kind: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Remove a relation recorded by ``adr_relate`` (ARG-001).

        A missing relation is an error, not a no-op. ``reason`` goes to the
        revision written on ``from_adr_id``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import (
            ADRNotFoundError,
            ADRRelationNotFoundError,
        )

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                adr_service.unrelate(
                    session,
                    project_id=project_id,
                    from_adr_id=from_adr_id,
                    to_adr_id=to_adr_id,
                    kind=kind,
                    reason=reason,
                    author="agent",
                )
                return {"from": from_adr_id, "to": to_adr_id, "kind": kind, "removed": True}
        except (ADRNotFoundError, ADRRelationNotFoundError) as exc:
            raise ValueError(str(exc)) from exc

    # ----------------------------------------------------------------- #
    # ARG-008 (RFC 34 §3.4): полки реестра ADR                            #
    # ----------------------------------------------------------------- #

    @mcp.tool(name="adr_topic_list")
    def adr_topic_list(project: str) -> list[dict[str, Any]]:
        """List the project's ADR topics («shelves») in their display order.

        Each row: ``{name, includes, excludes, position, adr_count}``. ADRs
        with no topic are not a row — they are ``topic: null`` in
        ``adr_list``. Pick a topic for ``adr_set_topic`` from this list by
        ``includes`` / ``excludes``; creating a new topic is a human decision.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_topic_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            counts = adr_topic_service.adr_counts(session, project_id)
            return [
                adr_topic_service.topic_to_dict(t, adr_count=counts.get(t.row_id, 0))
                for t in adr_topic_service.list_for_project(session, project_id)
            ]

    @mcp.tool(name="adr_topic_create")
    def adr_topic_create(
        project: str,
        name: str,
        includes: str = "",
        excludes: str = "",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Create an ADR topic at the end of the list (ARG-008).

        ``includes`` / ``excludes`` describe what belongs on the shelf and what
        goes elsewhere — the curator picks topics by them. A duplicate name is
        an error.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_topic_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            topic = adr_topic_service.create(
                session,
                project_id=project_id,
                name=name,
                includes=includes,
                excludes=excludes,
                author="agent",
                reason=reason,
            )
            return adr_topic_service.topic_to_dict(topic, adr_count=0)

    @mcp.tool(name="adr_topic_update")
    def adr_topic_update(
        project: str,
        name: str,
        new_name: str | None = None,
        includes: str | None = None,
        excludes: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Rename an ADR topic or edit its includes/excludes. ``None`` leaves a field as is."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_topic_service
        from cod_doc.services.adr_topic_service import ADRTopicNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                topic = adr_topic_service.update(
                    session,
                    project_id=project_id,
                    name=name,
                    new_name=new_name,
                    includes=includes,
                    excludes=excludes,
                    author="agent",
                    reason=reason,
                )
                return adr_topic_service.topic_to_dict(topic)
        except ADRTopicNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_topic_move")
    def adr_topic_move(
        project: str, name: str, position: int, reason: str | None = None
    ) -> dict[str, Any]:
        """Move an ADR topic to ``position`` (0-based); the others shift, no gaps."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_topic_service
        from cod_doc.services.adr_topic_service import ADRTopicNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                topic = adr_topic_service.move(
                    session,
                    project_id=project_id,
                    name=name,
                    position=position,
                    author="agent",
                    reason=reason,
                )
                return adr_topic_service.topic_to_dict(topic)
        except ADRTopicNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_topic_delete")
    def adr_topic_delete(project: str, name: str, reason: str | None = None) -> dict[str, Any]:
        """Delete an ADR topic. Its ADRs move to «no topic»; returns how many."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_topic_service
        from cod_doc.services.adr_topic_service import ADRTopicNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                moved = adr_topic_service.delete(
                    session, project_id=project_id, name=name, author="agent", reason=reason
                )
                return {"name": name, "deleted": True, "unshelved": moved}
        except ADRTopicNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_set_topic")
    def adr_set_topic(
        project: str, adr_id: str, topic: str | None = None, reason: str | None = None
    ) -> dict[str, Any]:
        """Put an ADR on a topic; ``topic=None`` moves it to «no topic» (ARG-008).

        Allowed in any status, including ACCEPTED: the topic is where the
        decision lies, not what it says. The topic must exist — see
        ``adr_topic_list``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import ADRNotFoundError
        from cod_doc.services.adr_topic_service import ADRTopicNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                row = adr_service.set_topic(
                    session,
                    project_id=project_id,
                    adr_id=adr_id,
                    topic=topic,
                    author="agent",
                    reason=reason,
                )
                return {"adr_id": row.adr_id, "topic": adr_service.topic_name(session, row)}
        except (ADRNotFoundError, ADRTopicNotFoundError) as exc:
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
                    session,
                    project_id=project_id,
                    adr_id=adr_id,
                    task_id=task_id,
                    relation=relation,
                    author="agent",
                )
                return {
                    "adr_id": adr_id,
                    "task_id": link.task_id,
                    "relation": link.relation,
                }
        except ADRNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_deprecate")
    def adr_deprecate(
        project: str,
        adr_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Transition an ADR (PROPOSED or ACCEPTED) to DEPRECATED.

        Idempotent on already-deprecated ADRs. Rejects terminal statuses
        other than ``deprecated`` (e.g. ``superseded``/``rejected``).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service
        from cod_doc.services.adr_service import ADRNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                row = adr_service.deprecate(
                    session,
                    project_id=project_id,
                    adr_id=adr_id,
                    reason=reason,
                    author="agent",
                )
                return adr_service.adr_to_dict(session, row)
        except ADRNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(name="adr_graph")
    def adr_graph(project: str) -> dict[str, Any]:
        """Return the ADR graph: ``{nodes: [...], edges: [...], relations: [...]}``.

        Each node carries adr_id + title + status; each ``edges`` item is a
        supersede edge with from/to (canonical ADR-NNN ids) + reason.
        ``relations`` (ARG-001) lists ``amends`` / ``depends_on`` links as
        from/to/kind/reason — they change no status. Suitable for direct
        Mermaid rendering.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import adr_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return adr_service.graph(session, project_id)
