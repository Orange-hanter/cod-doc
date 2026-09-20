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

    @mcp.tool(name="doc_node_intent_analyze")
    def doc_node_intent_analyze(
        project: str,
        author: str = "mcp",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Ask the model whether each section's documents cover its `intent`.

        The one question rules cannot answer. Everything countable — empty,
        thin, no intent, degenerate typing — is `doc_node_health_get` and needs
        no network.

        Writes to its own finding partition, so a failed run never closes the
        deterministic findings. An LLM error propagates: swallowing it would
        leave an empty verdict list, and the reconcile would read that as
        "every gap is fixed".

        Closing is hysteretic: a verdict that disappears for one run stays
        `open` and visible to the curator, and is only closed by a second
        consecutive miss. The model's verdict is subjective and flaps, so
        without the delay every borderline section would churn
        resolved/reopened on each run. The returned `missed` counts findings
        that were absent this run but not closed yet.

        Only sections the model actually ruled on are reconciled. The prompt
        asks for a verdict per section, but that is a request, not a
        guarantee: a valid answer can be incomplete, and silence about a
        section looks exactly like "fixed" — the fingerprint is absent either
        way. Findings of unanswered sections are left untouched; `unanswered`
        reports how many sections the model skipped, `skipped` how many
        findings were therefore not reconciled.
        """
        from cod_doc.api.deps import get_config
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_node_intent

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            out: dict[str, Any] = dict(
                doc_node_intent.analyze(
                    session,
                    project_id=project_id,
                    cfg=get_config(),
                    author=author,
                )
            )
        if dry_run:
            out["dry_run"] = True
        return out
