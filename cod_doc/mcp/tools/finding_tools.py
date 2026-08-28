"""MCP tools: finding.* — external findings (RFC 22 §3.3, SYM-006D).

Exposed under the ``standard``/``full`` profiles only — ``minimal`` and
``agent`` are explicit allowlists in ``cod_doc/mcp/profiles.py``, so the new
names never leak into them.

Activity events (proposal 09): ``finding_promote`` and ``finding_dismiss``
are writes. Both rely on the service layer to emit
(``finding_service.promote_finding`` → ``finding.promoted``;
``finding_service.dismiss_finding`` → ``finding.dismissed``) in the same
transaction — the wrappers deliberately do NOT emit a second event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register finding.* tools on the given FastMCP instance."""

    @mcp.tool(name="finding_list")
    def finding_list(
        project: str,
        status: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List external findings for a project, newest-seen first.

        status: open | resolved | dismissed | promoted (RFC 22 §3.2).
        source: ai_review | zairgrush | routine.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import finding_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return finding_service.list_findings(
                session, project_id, status=status, source=source, limit=limit
            )

    @mcp.tool(name="finding_get")
    def finding_get(project: str, finding_uid: str) -> dict[str, Any] | None:
        """Get a single finding by its stable ``finding_uid``. Returns null if not found."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import finding_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return finding_service.get_finding(session, project_id, finding_uid)

    @mcp.tool(name="finding_promote")
    def finding_promote(
        project: str,
        finding_uid: str,
        plan_scope: str,
        section_letter: str,
        on_finding: str = "create_task",
        id_prefix: str = "FND",
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Promote a finding into a task per the ``on_finding`` policy.

        on_finding: create_task | update_existing_task | comment_only
        (see cod_doc.services.routine_service.VALID_ON_FINDING).

        The underlying service emits the ``finding.promoted`` activity event
        itself; this wrapper does not double-emit (proposal 09).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository
        from cod_doc.services import finding_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            plan = PlanRepository(session).get_by_scope(plan_scope)
            if plan is None or plan.row_id is None:
                raise ValueError(f"Plan '{plan_scope}' not found.")
            sections = PlanSectionRepository(session).list_for_plan(plan.row_id)
            section = next(
                (s for s in sections if s.letter.upper() == section_letter.upper()), None
            )
            if section is None or section.row_id is None:
                letters = ", ".join(s.letter for s in sections)
                raise ValueError(f"Section '{section_letter}' not found. Available: {letters}")

            finding = finding_service.get_finding(session, project_id, finding_uid)
            if finding is None:
                raise ValueError(f"finding '{finding_uid}' not found")

            return finding_service.promote_finding(
                session,
                project_id=project_id,
                finding_id=finding["finding_id"],
                on_finding=on_finding,
                plan_id=plan.row_id,
                section_id=section.row_id,
                author=author,
                id_prefix=id_prefix,
            )

    @mcp.tool(name="finding_dismiss")
    def finding_dismiss(
        project: str,
        finding_uid: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Mark a finding ``dismissed`` (operator triage: not worth a task).

        The underlying service emits the ``finding.dismissed`` activity event
        itself; this wrapper does not double-emit (proposal 09). Dismissing an
        already-dismissed finding is a no-op.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import finding_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return finding_service.dismiss_finding(
                session,
                project_id=project_id,
                finding_uid=finding_uid,
                author=author,
                reason=reason,
            )
