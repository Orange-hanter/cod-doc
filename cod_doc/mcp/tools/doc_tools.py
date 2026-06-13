"""MCP tools: doc.* — document operations (COD-032)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import doc_to_dict, require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register doc.* tools on the given FastMCP instance."""

    @mcp.tool(name="doc_list")
    def doc_list(project: str) -> list[dict[str, Any]]:
        """List all documents for a project."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            docs = doc_service.list_for_project(session, project_id)
        return [doc_to_dict(d) for d in docs]

    @mcp.tool(name="doc_get")
    def doc_get(
        project: str,
        doc_key: str,
        include_sections: bool = False,
    ) -> dict[str, Any] | None:
        """Get document metadata. Set include_sections=true to also return section list."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None:
                return None
            result = doc_to_dict(d)
            if include_sections and d.row_id is not None:
                sections = doc_service.get_sections(session, d.row_id)
                result["sections"] = [
                    {
                        "anchor": s.anchor,
                        "heading": s.heading,
                        "level": s.level,
                        "position": s.position,
                    }
                    for s in sections
                ]
        return result

    @mcp.tool(name="doc_create")
    def doc_create(
        project: str,
        doc_key: str,
        type: str,
        status: str,
        title: str,
        owner: str | None = None,
        sensitivity: str = "internal",
        path: str | None = None,
        preamble: str = "",
        author: str = "mcp",
        reason: str | None = None,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a new document record.

        type: module-spec | module-subdoc | execution-plan | task-section |
              execution-log | standard | architecture | vision | guide |
              user-story | decision | open-question | redirect.
        status: draft | review | active | deprecated.
        sensitivity: public | internal | confidential | restricted.
        """
        from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
        from cod_doc.infra.db import transactional
        from cod_doc.mcp.tools import _idempotency
        from cod_doc.services import doc_service
        from cod_doc.services.validation import ValidationError

        cached = _idempotency.check("doc_create", idempotency_key)
        if cached is not None:
            return dict(cached, idempotent_replay=True)

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=not dry_run) as session:
                project_id = require_project_id(session, project)
                d = doc_service.create(
                    session,
                    project_id=project_id,
                    doc_key=doc_key,
                    type=DocumentType(type),
                    status=DocumentStatus(status),
                    title=title,
                    author=author,
                    path=path,
                    sensitivity=Sensitivity(sensitivity),
                    owner=owner,
                    preamble=preamble,
                    reason=reason,
                )
                from cod_doc.services import activity_service

                activity_service.emit(
                    session,
                    project_id,
                    "doc.created",
                    actor_kind="agent" if author.startswith("agent") else "human",
                    actor_id=author,
                    scope_kind="document",
                    scope_id=doc_key,
                    payload={"type": type, "status": status},
                    summary=f"Document {doc_key!r} created by {author}",
                )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        out = doc_to_dict(d)
        if dry_run:
            out["dry_run"] = True
        else:
            _idempotency.store("doc_create", idempotency_key, out)
        from cod_doc.services.skill_service import recommend_for_tool

        recs = recommend_for_tool("doc_create")
        if recs:
            out["recommended_skills"] = recs[:3]
        return out

    @mcp.tool(name="doc_rename")
    def doc_rename(
        project: str,
        doc_key: str,
        new_key: str,
        new_path: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
        cascade_links: bool = True,
    ) -> dict[str, Any]:
        """Rename a document (doc_key and optionally path). Cascades link updates by default."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            updated = doc_service.rename(
                session,
                document_id=d.row_id,
                new_doc_key=new_key,
                author=author,
                new_path=new_path,
                reason=reason,
                cascade_links=cascade_links,
            )
            from cod_doc.services import activity_service

            activity_service.emit(
                session,
                project_id,
                "doc.renamed",
                actor_kind="agent" if author.startswith("agent") else "human",
                actor_id=author,
                scope_kind="document",
                scope_id=new_key,
                payload={"old_key": doc_key, "new_key": new_key},
                summary=f"Document renamed {doc_key!r} → {new_key!r}",
            )
        return doc_to_dict(updated)

    @mcp.tool(name="doc_body")
    def doc_body(project: str, doc_key: str) -> str:
        """Return the full rendered body of a document (preamble + all sections)."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            body = doc_service.render_body(session, d.row_id)
        return body or ""

    @mcp.tool(name="doc_accept")
    def doc_accept(
        project: str,
        doc_key: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """COD-052: promote a DRAFT/REVIEW document to ACTIVE (the 'accept' step).

        Writes a DOCUMENT revision and emits a ``doc.accepted`` activity event.
        Accepting an already-ACTIVE document is a no-op transition.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service, doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            updated = doc_service.accept(
                session, document_id=d.row_id, author=author, reason=reason
            )
            activity_service.emit(
                session,
                project_id,
                "doc.accepted",
                actor_kind="agent" if author.startswith("agent") else "human",
                actor_id=author,
                scope_kind="document",
                scope_id=doc_key,
                payload={"status": updated.status.value},
                summary=f"Document {doc_key!r} accepted → {updated.status.value}",
            )
        return doc_to_dict(updated)

    @mcp.tool(name="doc_export")
    def doc_export(
        project: str,
        doc_key: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Export a document projection to disk. Returns {path, written, content_hash}.
        Skips if projection_hash already matches current DB content (unless force=true).
        """
        from pathlib import Path

        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service, projection_service

        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            result = projection_service.export_document(
                session, d.row_id, root_path=root, force=force
            )
        return {
            "path": str(result.path),
            "written": result.written,
            "content_hash": result.content_hash,
        }

    @mcp.tool(name="doc_drift")
    def doc_drift(project: str, doc_key: str) -> dict[str, Any]:
        """Detect drift between DB content, projection_hash, and the on-disk file.
        status: in_sync | stale_export | edited_in_place | missing.
        """
        from pathlib import Path

        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service, projection_service

        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            report = projection_service.detect_drift(session, d.row_id, root_path=root)
        return {
            "doc_key": doc_key,
            "status": report.status.value,
            "projection_hash": report.projection_hash,
            "db_content_hash": report.db_content_hash,
            "file_hash": report.file_hash,
        }

    @mcp.tool(name="doc_drift_all")
    def doc_drift_all(project: str, limit: int | None = None) -> dict[str, Any]:
        """Project-wide DB↔markdown drift summary.

        Returns counts by status plus issue rows for non-in-sync documents.
        """
        from pathlib import Path

        from cod_doc.infra.db import transactional
        from cod_doc.services import projection_service

        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            report = projection_service.detect_project_drift(
                session,
                project_id,
                root_path=root,
                limit=limit,
            )
        return {
            "project": project,
            "total_docs": report.total_docs,
            "problem_count": report.problem_count,
            "counts": report.counts,
            "issues": [
                {
                    "doc_key": item.doc_key,
                    "path": item.path,
                    "status": item.report.status.value,
                    "projection_hash": item.report.projection_hash,
                    "db_content_hash": item.report.db_content_hash,
                    "file_hash": item.report.file_hash,
                }
                for item in report.issues
            ],
        }
