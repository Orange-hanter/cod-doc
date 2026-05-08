"""MCP tools: task_doc.* — task-bound structured documents (PCA-101, proposal 05).

Canonical keys: 'plan', 'design', 'verification', 'acceptance', or custom.
Every write creates a revision (entity_kind='task_doc') tagged with the
active run_id so it appears in ``run_get`` output.

Tools
-----
- ``task_doc_get``       — read a single doc by task_id + key
- ``task_doc_put``       — create or update (optimistic lock via base_revision_id)
- ``task_doc_list``      — all docs pinned to a task
- ``task_doc_revisions`` — revision history for a doc
- ``task_doc_revert``    — restore body to a past revision
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _resolve_task_row_id(session: Any, project_id: int, task_id: str) -> int:
    from sqlalchemy import select
    from cod_doc.infra.models import TaskModel

    row = session.execute(
        select(TaskModel.row_id).where(
            TaskModel.project_id == project_id,
            TaskModel.task_id == task_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"Task '{task_id}' not found.")
    return int(row)


def _doc_to_dict(doc: Any) -> dict[str, Any]:
    return {
        "task_row_id": doc.task_id,
        "key": doc.key,
        "title": doc.title,
        "body": doc.body,
        "format": doc.format,
        "current_revision_id": doc.current_revision_id,
        "created": doc.created.isoformat() if doc.created else None,
        "last_updated": doc.last_updated.isoformat() if doc.last_updated else None,
    }


def register(mcp: FastMCP) -> None:
    """Register task_doc.* tools on the given FastMCP instance."""

    @mcp.tool(name="task_doc_get")
    def task_doc_get(project: str, task_id: str, key: str) -> dict[str, Any] | None:
        """Get a task-bound document by task_id and key. Returns null if not found."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            task_row_id = _resolve_task_row_id(session, project_id, task_id)
            doc = task_doc_service.get(session, task_row_id, key)
        return _doc_to_dict(doc) if doc is not None else None

    @mcp.tool(name="task_doc_put")
    def task_doc_put(
        project: str,
        task_id: str,
        key: str,
        title: str,
        body: str,
        base_revision_id: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
        format: str = "markdown",
    ) -> dict[str, Any]:
        """Create or update a task-bound document.

        Canonical keys: 'plan', 'design', 'verification', 'acceptance', or custom.
        Pass ``base_revision_id`` (the current_revision_id you last observed) to
        enable optimistic locking — the write is rejected if another writer landed
        first.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_doc_service
        from cod_doc.services.task_doc_service import TaskDocConflictError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                task_row_id = _resolve_task_row_id(session, project_id, task_id)
                doc = task_doc_service.put(
                    session,
                    project_id=project_id,
                    task_row_id=task_row_id,
                    key=key,
                    title=title,
                    body=body,
                    base_revision_id=base_revision_id,
                    author=author,
                    reason=reason,
                    format=format,
                )
        except TaskDocConflictError as exc:
            raise ValueError(str(exc)) from exc
        return _doc_to_dict(doc)

    @mcp.tool(name="task_doc_list")
    def task_doc_list(project: str, task_id: str) -> list[dict[str, Any]]:
        """List all documents pinned to a task (body omitted for compactness)."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            task_row_id = _resolve_task_row_id(session, project_id, task_id)
            docs = task_doc_service.list_for_task(session, task_row_id)
        return [
            {
                "key": d.key,
                "title": d.title,
                "format": d.format,
                "current_revision_id": d.current_revision_id,
                "last_updated": d.last_updated.isoformat() if d.last_updated else None,
            }
            for d in docs
        ]

    @mcp.tool(name="task_doc_revisions")
    def task_doc_revisions(
        project: str, task_id: str, key: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return revision history for a task document (oldest first)."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            task_row_id = _resolve_task_row_id(session, project_id, task_id)
            revs = task_doc_service.revisions(session, task_row_id, key, limit=limit)
        return revs

    @mcp.tool(name="task_doc_revert")
    def task_doc_revert(
        project: str,
        task_id: str,
        key: str,
        revision_id: str,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Restore a task document to the body recorded in a specific revision."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_doc_service

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                task_row_id = _resolve_task_row_id(session, project_id, task_id)
                doc = task_doc_service.revert(
                    session,
                    project_id=project_id,
                    task_row_id=task_row_id,
                    key=key,
                    revision_id=revision_id,
                    author=author,
                )
        except LookupError as exc:
            raise ValueError(str(exc)) from exc
        return _doc_to_dict(doc)
