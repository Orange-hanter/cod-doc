"""MCP tools: link.* — document link operations (COD-032)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _resolve_section_id(session: Any, project_id: int, doc_key: str, anchor: str) -> int:
    from sqlalchemy import select

    from cod_doc.infra.models import DocumentModel, SectionModel

    doc_row = session.execute(
        select(DocumentModel.row_id).where(
            DocumentModel.project_id == project_id,
            DocumentModel.doc_key == doc_key,
        )
    ).scalar_one_or_none()
    if doc_row is None:
        raise ValueError(f"Document '{doc_key}' not found.")

    sec_row = session.execute(
        select(SectionModel.row_id).where(
            SectionModel.document_id == doc_row,
            SectionModel.anchor == anchor,
        )
    ).scalar_one_or_none()
    if sec_row is None:
        raise ValueError(f"Section '{doc_key}#{anchor}' not found.")
    return int(sec_row)


def register(mcp: FastMCP) -> None:
    """Register link.* tools on the given FastMCP instance."""

    @mcp.tool(name="link.list")
    def link_list(
        project: str,
        doc_key: str,
        anchor: str | None = None,
    ) -> list[dict[str, Any]]:
        """List links in a document (all sections) or a single section (anchor=<anchor>).
        Returns resolved/broken status, target, and kind for each link.
        """
        from sqlalchemy import select

        from cod_doc.infra.db import transactional
        from cod_doc.infra.models import DocumentModel, SectionModel
        from cod_doc.services import link_service

        sf, _ = session_factory(project)
        links = []
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            doc_row = session.execute(
                select(DocumentModel.row_id).where(
                    DocumentModel.project_id == project_id,
                    DocumentModel.doc_key == doc_key,
                )
            ).scalar_one_or_none()
            if doc_row is None:
                raise ValueError(f"Document '{doc_key}' not found.")

            if anchor:
                sec_id = _resolve_section_id(session, project_id, doc_key, anchor)
                links = link_service.list_for_section(session, sec_id)
            else:
                sec_ids = (
                    session.execute(
                        select(SectionModel.row_id).where(SectionModel.document_id == doc_row)
                    )
                    .scalars()
                    .all()
                )
                for sid in sec_ids:
                    links.extend(link_service.list_for_section(session, int(sid)))

        return [
            {
                "raw": lk.raw,
                "kind": lk.kind.value,
                "resolved": lk.resolved,
                "to_doc_key": lk.to_doc_key,
                "to_task_id": lk.to_task_id,
                "to_story_id": lk.to_story_id,
                "broken_reason": lk.broken_reason,
            }
            for lk in links
        ]

    @mcp.tool(name="link.sync")
    def link_sync(project: str, doc_key: str, anchor: str) -> dict[str, Any]:
        """Re-parse section body and sync link rows. Returns count of synced links."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import link_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            sec_id = _resolve_section_id(session, project_id, doc_key, anchor)
            links = link_service.sync_section(session, sec_id)
        return {"doc_key": doc_key, "anchor": anchor, "synced": len(links)}

    @mcp.tool(name="link.verify")
    def link_verify(project: str, doc_key: str, anchor: str) -> dict[str, Any]:
        """Verify link resolution for a section. Returns ok/broken/skipped counts.
        broken > 0 indicates broken references that need attention.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import link_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            sec_id = _resolve_section_id(session, project_id, doc_key, anchor)
            report = link_service.verify_section(session, sec_id)
        return {
            "doc_key": doc_key,
            "anchor": anchor,
            "ok": report.ok,
            "broken": report.broken,
            "skipped": report.skipped,
        }
