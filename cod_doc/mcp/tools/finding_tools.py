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

#: Потолок ``limit`` для клиента: каждая строка материализуется в память и в
#: JSON-ответ общего демона, а ``limit=-1`` в SQLite значит «без предела».
MAX_LIST_LIMIT = 500


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
        limit: clamped to 1..500.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import finding_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return finding_service.list_findings(
                session,
                project_id,
                status=status,
                source=source,
                limit=max(1, min(limit, MAX_LIST_LIMIT)),
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
        from cod_doc.services import finding_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            return finding_service.promote_finding_by_ref(
                session,
                project_id=require_project_id(session, project),
                finding_uid=finding_uid,
                plan_scope=plan_scope,
                section_letter=section_letter,
                on_finding=on_finding,
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
