"""TaskDocumentService — task-bound structured documents (PCA-101, proposal 05).

Each task can have multiple named documents (key='plan', 'design',
'verification', 'acceptance', or custom). Every put creates a revision
in the shared `revision` table (entity_kind='task_doc').

Revision diff format: JSON ``{"op": "put", "body": "<full body>"}`` so that
revert is a simple read-and-restore rather than diff replay.

Public API
----------
- ``get(session, task_row_id, key)`` → ``TaskDocument | None``
- ``list_for_task(session, task_row_id)`` → ``list[TaskDocument]``
- ``put(session, project_id, task_row_id, key, title, body, …)`` → ``TaskDocument``
- ``revisions(session, task_row_id, key, limit)`` → revision list
- ``revert(session, project_id, task_row_id, key, revision_id, author)``
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from ulid import ULID

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.models import RevisionModel, TaskDocumentModel
from cod_doc.services import activity_service
from cod_doc.services.run_context import get_current_run_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class TaskDocConflictError(RuntimeError):
    """Raised when ``base_revision_id`` is stale (optimistic lock failure)."""


@dataclass
class TaskDocument:
    row_id: int
    task_id: int
    key: str
    title: str
    body: str
    format: str
    current_revision_id: str | None
    created: datetime
    last_updated: datetime


def _to_domain(m: TaskDocumentModel) -> TaskDocument:
    return TaskDocument(
        row_id=m.row_id,
        task_id=m.task_id,
        key=m.key,
        title=m.title,
        body=m.body,
        format=m.format,
        current_revision_id=m.current_revision_id,
        created=m.created,
        last_updated=m.last_updated,
    )


def _write_revision(
    session: Session,
    project_id: int,
    task_doc: TaskDocumentModel,
    body: str,
    author: str,
    reason: str,
) -> str:
    """Append a snapshot revision; return new revision_id."""
    new_rev_id = str(ULID())
    diff = json.dumps({"op": "put", "body": body})
    session.add(
        RevisionModel(
            revision_id=new_rev_id,
            project_id=project_id,
            entity_kind=EntityKind.TASK_DOC.value,
            entity_id=task_doc.row_id,
            parent_revision_id=task_doc.current_revision_id,
            author=author,
            diff=diff,
            reason=reason,
            run_id=get_current_run_id(),
        )
    )
    return new_rev_id


def get(session: Session, task_row_id: int, key: str) -> TaskDocument | None:
    m = session.execute(
        select(TaskDocumentModel).where(
            TaskDocumentModel.task_id == task_row_id,
            TaskDocumentModel.key == key,
        )
    ).scalar_one_or_none()
    return _to_domain(m) if m is not None else None


def list_for_task(session: Session, task_row_id: int) -> list[TaskDocument]:
    rows = session.execute(
        select(TaskDocumentModel)
        .where(TaskDocumentModel.task_id == task_row_id)
        .order_by(TaskDocumentModel.key)
    ).scalars()
    return [_to_domain(m) for m in rows]


def put(
    session: Session,
    *,
    project_id: int,
    task_row_id: int,
    key: str,
    title: str,
    body: str,
    author: str = "mcp",
    reason: str | None = None,
    base_revision_id: str | None = None,
    format: str = "markdown",
) -> TaskDocument:
    """Create or update a task document.

    If ``base_revision_id`` is provided and does not match the current head,
    raises :class:`TaskDocConflictError` (optimistic lock).
    """
    m = session.execute(
        select(TaskDocumentModel).where(
            TaskDocumentModel.task_id == task_row_id,
            TaskDocumentModel.key == key,
        )
    ).scalar_one_or_none()

    is_new = m is None
    if m is None:
        if base_revision_id is not None:
            raise TaskDocConflictError(
                f"base_revision_id={base_revision_id!r} provided but document does not exist yet."
            )
        m = TaskDocumentModel(task_id=task_row_id, key=key, title=title, body=body, format=format)
        session.add(m)
        session.flush()
        new_rev_id = _write_revision(session, project_id, m, body, author, reason or "create")
    else:
        if base_revision_id is not None and m.current_revision_id != base_revision_id:
            raise TaskDocConflictError(
                f"Conflict: expected head={base_revision_id!r}, "
                f"current={m.current_revision_id!r}. Re-read and retry."
            )
        m.title = title
        m.body = body
        m.format = format
        m.last_updated = datetime.now(UTC)
        session.flush()
        new_rev_id = _write_revision(session, project_id, m, body, author, reason or "update")

    m.current_revision_id = new_rev_id
    session.flush()
    activity_service.emit_for_write(
        session,
        project_id,
        "task_doc.created" if is_new else "task_doc.updated",
        author,
        scope_kind="task_doc",
        scope_id=f"{task_row_id}:{key}",
        payload={"key": key, "title": title, "format": format},
        summary=f"Task doc {key} {'created' if is_new else 'updated'}",
    )
    return _to_domain(m)


def revisions(
    session: Session,
    task_row_id: int,
    key: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    m = session.execute(
        select(TaskDocumentModel).where(
            TaskDocumentModel.task_id == task_row_id,
            TaskDocumentModel.key == key,
        )
    ).scalar_one_or_none()
    if m is None:
        return []

    rows = session.execute(
        select(RevisionModel)
        .where(
            RevisionModel.entity_kind == EntityKind.TASK_DOC.value,
            RevisionModel.entity_id == m.row_id,
        )
        .order_by(RevisionModel.at.asc(), RevisionModel.row_id.asc())
        .limit(limit)
    ).scalars()
    return [
        {
            "revision_id": r.revision_id,
            "author": r.author,
            "at": r.at.isoformat() if r.at else None,
            "reason": r.reason,
            "parent_revision_id": r.parent_revision_id,
            "run_id": r.run_id,
        }
        for r in rows
    ]


def revert(
    session: Session,
    *,
    project_id: int,
    task_row_id: int,
    key: str,
    revision_id: str,
    author: str = "mcp",
) -> TaskDocument:
    """Restore a task document to the body recorded in a specific revision."""
    m = session.execute(
        select(TaskDocumentModel).where(
            TaskDocumentModel.task_id == task_row_id,
            TaskDocumentModel.key == key,
        )
    ).scalar_one_or_none()
    if m is None:
        raise LookupError(f"task_doc key={key!r} not found for task_row_id={task_row_id}")

    target_rev = session.execute(
        select(RevisionModel).where(
            RevisionModel.revision_id == revision_id,
            RevisionModel.entity_kind == EntityKind.TASK_DOC.value,
            RevisionModel.entity_id == m.row_id,
        )
    ).scalar_one_or_none()
    if target_rev is None:
        raise LookupError(f"Revision {revision_id!r} not found for this task_doc.")

    payload = json.loads(target_rev.diff)
    body_at_target = payload.get("body", "")

    m.body = body_at_target
    m.last_updated = datetime.now(UTC)
    session.flush()
    new_rev_id = _write_revision(
        session,
        project_id,
        m,
        body_at_target,
        author,
        reason=f"revert to {revision_id}",
    )
    m.current_revision_id = new_rev_id
    session.flush()
    activity_service.emit_for_write(
        session,
        project_id,
        "task_doc.reverted",
        author,
        scope_kind="task_doc",
        scope_id=f"{task_row_id}:{key}",
        payload={"key": key, "revision_id": revision_id},
        summary=f"Task doc {key} reverted to {revision_id}",
    )
    return _to_domain(m)
