"""MCP tools: doc_tree_* / doc_node_* — the documentation tree (ADO-116).

One file per tool family, per the repository's MCP convention. The read half
answers "what sections exist and what is still unplaced"; the write half seeds
the tree, edits sections and files documents into them.

Every write mirrors a CLI command under ``cod-doc doc tree`` — a mutation that
exists on only one surface is exactly what
``tests/services/test_doc_mutation_surface_parity.py`` forbids.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from cod_doc.domain.entities import DocNode
    from cod_doc.services.doc_tree_service import NodeStat


def _node_to_dict(node: DocNode) -> dict[str, Any]:
    return {
        "node_key": node.node_key,
        "title": node.title,
        "intent": node.intent,
        "position": node.position,
        "parent_id": node.parent_id,
        "expected_types": list(node.expected_types),
        "min_docs": node.min_docs,
        "is_inbox": node.is_inbox,
    }


def _stat_to_dict(stat: NodeStat) -> dict[str, Any]:
    out = _node_to_dict(stat.node)
    out["doc_count"] = stat.doc_count
    out["under_filled"] = stat.under_filled
    return out


def register(mcp: FastMCP) -> None:
    """Register doc_tree_* / doc_node_* tools on the given FastMCP instance."""

    @mcp.tool(name="doc_tree_get")
    def doc_tree_get(project: str) -> dict[str, Any]:
        """Documentation tree: sections with document counts, plus what is unplaced.

        `unplaced` counts documents with no section — the Inbox. A non-zero
        value means the corpus is not filed yet, not that something is broken.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_tree_service as tree

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            stats = tree.node_stats(session, project_id)
            unplaced = tree.unplaced_count(session, project_id)
            return {
                "nodes": [_stat_to_dict(s) for s in stats],
                "unplaced": unplaced,
                "seeded": bool(stats),
            }

    @mcp.tool(name="doc_tree_unplaced")
    def doc_tree_unplaced(project: str) -> dict[str, Any]:
        """Documents that are not filed into any section yet — the Inbox queue."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_tree_service as tree

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            keys = tree.unplaced(session, project_id)
        return {"count": len(keys), "doc_keys": keys}

    @mcp.tool(name="doc_tree_init")
    def doc_tree_init(
        project: str,
        author: str = "mcp",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Seed the default section tree. Idempotent: existing keys are left alone.

        Returns the sections it created — an empty list means the tree was
        already there.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_tree_service as tree

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            created = tree.init_tree(session, project_id=project_id, author=author)
            out: dict[str, Any] = {
                "created": [_node_to_dict(n) for n in created],
                "created_count": len(created),
            }
        if dry_run:
            out["dry_run"] = True
        return out

    @mcp.tool(name="doc_tree_classify")
    def doc_tree_classify(
        project: str,
        dry_run: bool = True,
        only_unplaced: bool = True,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """File documents into sections by the deterministic rules.

        `dry_run=True` (the default) computes the placement and writes nothing.
        `only_unplaced=True` (the default) never overrules a section a human
        picked by hand; pass false to re-file the whole corpus.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_tree_service as tree

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            report = tree.classify_project(
                session,
                project_id=project_id,
                author=author,
                dry_run=dry_run,
                only_unplaced=only_unplaced,
            )
            out: dict[str, Any] = {
                "placed": len(report.placed),
                "unplaced": len(report.unplaced),
                "by_node": report.by_node,
                "unplaced_docs": [
                    {"doc_key": p.doc_key, "reason": p.reason} for p in report.unplaced
                ],
            }
        if dry_run:
            out["dry_run"] = True
        return out

    @mcp.tool(name="doc_set_node")
    def doc_set_node(
        project: str,
        doc_key: str,
        node_key: str | None = None,
        author: str = "mcp",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """File one document into a section. `node_key=null` returns it to the Inbox."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_tree_service as tree

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            node = tree.assign(
                session,
                project_id=project_id,
                doc_key=doc_key,
                node_key=node_key,
                author=author,
            )
            out: dict[str, Any] = {
                "doc_key": doc_key,
                "node_key": node.node_key if node is not None else None,
            }
        if dry_run:
            out["dry_run"] = True
        return out

    @mcp.tool(name="doc_node_create")
    def doc_node_create(
        project: str,
        node_key: str,
        title: str,
        intent: str = "",
        parent_key: str | None = None,
        position: int | None = None,
        min_docs: int = 0,
        author: str = "mcp",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Add a section to the tree. `intent` says what belongs in it — write it."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_tree_service as tree

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            node = tree.create_node(
                session,
                project_id=project_id,
                node_key=node_key,
                title=title,
                intent=intent,
                parent_key=parent_key,
                position=position,
                min_docs=min_docs,
                author=author,
            )
            out = _node_to_dict(node)
        if dry_run:
            out["dry_run"] = True
        return out

    @mcp.tool(name="doc_node_update")
    def doc_node_update(
        project: str,
        node_key: str,
        title: str | None = None,
        intent: str | None = None,
        position: int | None = None,
        min_docs: int | None = None,
        author: str = "mcp",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Edit a section. Omitted fields are left as they are; a no-op writes nothing."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_tree_service as tree

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            node = tree.update_node(
                session,
                project_id=project_id,
                node_key=node_key,
                title=title,
                intent=intent,
                position=position,
                min_docs=min_docs,
                author=author,
            )
            out = _node_to_dict(node)
        if dry_run:
            out["dry_run"] = True
        return out

    @mcp.tool(name="doc_node_delete")
    def doc_node_delete(
        project: str,
        node_key: str,
        reassign_to: str | None = None,
        force: bool = False,
        author: str = "mcp",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove a section.

        A section holding documents is not dropped silently: pass `reassign_to`
        to move them, or `force=true` to send them back to the Inbox.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_tree_service as tree

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            moved = tree.delete_node(
                session,
                project_id=project_id,
                node_key=node_key,
                reassign_to=reassign_to,
                force=force,
                author=author,
            )
            out: dict[str, Any] = {
                "node_key": node_key,
                "moved": moved,
                "reassign_to": reassign_to,
            }
        if dry_run:
            out["dry_run"] = True
        return out
