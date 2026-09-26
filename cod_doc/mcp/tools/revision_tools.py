"""MCP tools: revision.* — revision history (COD-032)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_KIND_MAP = {
    "document": "document",
    "section": "section",
    "task": "task",
    "plan": "plan",
    "story": "story",
    "link": "link",
    "module": "module",
}


def _resolve_entity_id(session: Any, kind: str, ref: str, project_id: int) -> int:
    """Resolve a user-facing ref to an entity row_id."""
    from sqlalchemy import select

    from cod_doc.infra.models import DocumentModel, SectionModel, TaskModel, UserStoryModel

    if kind == "task":
        row = session.execute(
            select(TaskModel.row_id).where(
                TaskModel.project_id == project_id, TaskModel.task_id == ref
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Task '{ref}' not found.")
        return int(row)

    if kind == "document":
        row = session.execute(
            select(DocumentModel.row_id).where(
                DocumentModel.project_id == project_id, DocumentModel.doc_key == ref
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Document '{ref}' not found.")
        return int(row)

    if kind == "story":
        row = session.execute(
            select(UserStoryModel.row_id).where(
                UserStoryModel.project_id == project_id, UserStoryModel.story_id == ref
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Story '{ref}' not found.")
        return int(row)

    if kind == "section":
        if "#" not in ref:
            raise ValueError("Section ref must be 'doc_key#anchor'.")
        doc_key, anchor = ref.split("#", 1)
        doc_row = session.execute(
            select(DocumentModel.row_id).where(
                DocumentModel.project_id == project_id, DocumentModel.doc_key == doc_key
            )
        ).scalar_one_or_none()
        if doc_row is None:
            raise ValueError(f"Document '{doc_key}' not found.")
        row = session.execute(
            select(SectionModel.row_id).where(
                SectionModel.document_id == doc_row, SectionModel.anchor == anchor
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Section '{ref}' not found.")
        return int(row)

    try:
        return int(ref)
    except ValueError:
        raise ValueError(f"For kind='{kind}', ref must be an integer row_id.") from None


def register(mcp: FastMCP) -> None:
    """Register revision.* tools on the given FastMCP instance."""

    @mcp.tool(name="revision_list")
    def revision_list(
        project: str,
        kind: str | None = None,
        ref: str | None = None,
        limit: int = 20,
        since: str | None = None,
        author: str | None = None,
        entity_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """List revisions: entity history (kind+ref) or project feed (neither).

        Entity mode — kind and ref both given: history of one entity, newest
        last, up to limit; rows carry revision_id, author, at, reason,
        parent_revision_id, diff.
        kind: task | document | section | story | plan | link | module.
        ref: task_id / doc_key / story_id / doc_key#anchor / integer row_id.

        Feed mode — kind and ref both omitted: newest-first project feed with
        optional filters since (ISO date), author, entity_kind. Rows carry
        revision_id, entity_kind, entity_id, author, at, reason — no diff
        (fetch it via revision_get). RFC 27 F9: «ревизии за сегодня» —
        revision_list(project, since='2026-09-26', limit=200), группировка
        по entity_kind на стороне клиента.
        """
        from cod_doc.domain.entities import EntityKind
        from cod_doc.infra.db import transactional
        from cod_doc.services import revision_service
        from cod_doc.services.task_service import parse_since

        if (kind is None) != (ref is None):
            raise ValueError("kind and ref must be given together: either both or neither.")

        sf, _ = session_factory(project)
        if kind is not None and ref is not None:
            if since is not None or author is not None or entity_kind is not None:
                raise ValueError(
                    "Feed filters (since/author/entity_kind) cannot be combined "
                    "with kind+ref entity mode."
                )
            if kind not in _KIND_MAP:
                raise ValueError(f"Invalid kind '{kind}'. Choose from: {', '.join(_KIND_MAP)}")
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                entity_id = _resolve_entity_id(session, kind, ref, project_id)
                revisions = revision_service.list_for_entity(
                    session, EntityKind(kind), entity_id, limit=limit
                )
            return [
                {
                    "revision_id": r.revision_id,
                    "author": r.author,
                    "at": r.at.isoformat() if r.at else None,
                    "reason": r.reason,
                    "parent_revision_id": r.parent_revision_id,
                    "diff": r.diff,
                }
                for r in revisions
            ]

        if entity_kind is not None and entity_kind not in _KIND_MAP:
            raise ValueError(
                f"Invalid entity_kind '{entity_kind}'. Choose from: {', '.join(_KIND_MAP)}"
            )
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            revisions = revision_service.list_for_project(
                session,
                project_id,
                limit=limit,
                entity_kind=EntityKind(entity_kind) if entity_kind else None,
                since=parse_since(since) if since else None,
                author=author,
            )
        return [
            {
                "revision_id": r.revision_id,
                "entity_kind": r.entity_kind.value,
                "entity_id": r.entity_id,
                "author": r.author,
                "at": r.at.isoformat() if r.at else None,
                "reason": r.reason,
            }
            for r in revisions
        ]

    @mcp.tool(name="revision_get")
    def revision_get(project: str, revision_id: str) -> dict[str, Any] | None:
        """Get a single revision by its ULID revision_id. Returns null if not found."""
        from sqlalchemy import select

        from cod_doc.infra.db import transactional
        from cod_doc.infra.models import RevisionModel
        from cod_doc.services import revision_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            model = session.execute(
                select(RevisionModel).where(RevisionModel.revision_id == revision_id)
            ).scalar_one_or_none()
            if model is None:
                return None
            r = revision_service._to_domain(model)

        return {
            "revision_id": r.revision_id,
            "entity_kind": r.entity_kind.value,
            "entity_id": r.entity_id,
            "author": r.author,
            "at": r.at.isoformat() if r.at else None,
            "reason": r.reason,
            "parent_revision_id": r.parent_revision_id,
            "commit_sha": r.commit_sha,
            "diff": r.diff,
        }

    @mcp.tool(name="revision_revert")
    def revision_revert(
        project: str,
        revision_id: str,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Revert a revision by creating an inverse revision (history is append-only).
        Supported: TASK status/complete, SECTION unified-diff, DOCUMENT rename.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import revision_service
        from cod_doc.services.revision_service import RevertNotSupportedError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                require_project_id(session, project)
                new_rev = revision_service.revert(session, revision_id, author=author)
        except LookupError:
            raise ValueError(f"Revision '{revision_id}' not found.") from None
        except RevertNotSupportedError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "reverted": revision_id,
            "new_revision_id": new_rev.revision_id,
            "author": new_rev.author,
        }
