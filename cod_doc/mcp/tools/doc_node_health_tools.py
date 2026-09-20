"""MCP tools: doc_node_health_* — gaps in what the corpus actually says.

The documentation tree (ADO-116) answers "where does it live"; this family
answers "what is not written". Both read the same ``doc_node``.

Findings land in the shared ``finding`` table, so a gap shows up in
``curator_next`` and can be turned into a task with ``finding_promote``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from cod_doc.services.doc_node_health import HealthIssue


def _issue_to_dict(issue: HealthIssue) -> dict[str, Any]:
    return {
        "code": issue.code,
        "scope_kind": issue.scope_kind,
        "scope_id": issue.scope_id,
        "title": issue.title,
        "body": issue.body,
        "severity": issue.severity,
    }


def register(mcp: FastMCP) -> None:
    """Register doc_node_health_* tools on the given FastMCP instance."""

    @mcp.tool(name="doc_node_health_get")
    def doc_node_health_get(project: str) -> dict[str, Any]:
        """Gaps in section fill-level, computed now. Read-only, no LLM.

        `seeded=false` means the tree was never created — then "no gaps" says
        nothing, and the fix is `doc_tree_init`, not writing documents.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_node_health

        sf, _ = session_factory(project)
        with transactional(sf, commit=False) as session:
            project_id = require_project_id(session, project)
            seeded = doc_node_health.tree_is_seeded(session, project_id)
            issues = (
                doc_node_health.assess(session, project_id, project_slug=project) if seeded else []
            )
            return {
                "seeded": seeded,
                "count": len(issues),
                "issues": [_issue_to_dict(i) for i in issues],
            }

    @mcp.tool(name="doc_node_health_sync")
    def doc_node_health_sync(
        project: str,
        author: str = "mcp",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Persist the gaps as findings; close the ones that got fixed.

        Idempotent: a second run bumps `times_seen` instead of duplicating
        rows. A section that was filled has its finding resolved; a section
        that regressed has it reopened.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_node_health

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            result = doc_node_health.sync(
                session,
                project_id=project_id,
                project_slug=project,
                author=author,
            )
            out: dict[str, Any] = dict(result.as_dict())
        if dry_run:
            out["dry_run"] = True
        return out
