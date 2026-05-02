"""Story-to-(task|document|module) links — create with target validation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import (
    EntityKind,
    StoryLink,
    StoryLinkKind,
    StoryRelation,
)
from cod_doc.infra.models import (
    DocumentModel,
    ModuleModel,
    StoryLinkModel,
    TaskModel,
)
from cod_doc.infra.repositories import StoryLinkRepository
from cod_doc.services import revision_service as rev

from ._internals import _diff, _require_story
from ._types import BrokenLinkError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _validate_link_target(
    session: Session, project_id: int, to_kind: StoryLinkKind, to_ref: str
) -> None:
    """Raise BrokenLinkError if the target doesn't exist in the project."""
    if to_kind is StoryLinkKind.TASK:
        stmt = select(TaskModel.row_id).where(
            TaskModel.project_id == project_id, TaskModel.task_id == to_ref
        )
    elif to_kind is StoryLinkKind.DOCUMENT:
        stmt = select(DocumentModel.row_id).where(
            DocumentModel.project_id == project_id, DocumentModel.doc_key == to_ref
        )
    elif to_kind is StoryLinkKind.MODULE:
        stmt = select(ModuleModel.row_id).where(
            ModuleModel.project_id == project_id, ModuleModel.module_id == to_ref
        )
    else:
        raise BrokenLinkError(f"unsupported link kind: {to_kind!r}")
    if session.execute(stmt).scalar_one_or_none() is None:
        raise BrokenLinkError(f"{to_kind.value} not found: {to_ref}")


def link(
    session: Session,
    *,
    story_id: str,
    to_kind: StoryLinkKind,
    to_ref: str,
    relation: StoryRelation,
    author: str,
    reason: str | None = None,
) -> StoryLink:
    """Attach a story to a task/document/module. Idempotent on the (kind, ref, relation) edge."""
    model = _require_story(session, story_id)
    _validate_link_target(session, model.project_id, to_kind, to_ref)

    # De-dup: same (kind, ref, relation) edge → return existing.
    stmt = select(StoryLinkModel).where(
        StoryLinkModel.story_id == model.row_id,
        StoryLinkModel.to_kind == to_kind.value,
        StoryLinkModel.to_ref == to_ref,
        StoryLinkModel.relation == relation.value,
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing is not None:
        return StoryLinkRepository(session)._to_domain(existing)

    new_link = StoryLinkRepository(session).add(
        StoryLink(
            story_id=model.row_id,
            to_kind=to_kind,
            to_ref=to_ref,
            relation=relation,
        )
    )
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.STORY,
        entity_id=model.row_id,
        author=author,
        diff=_diff(
            "link",
            to_kind=to_kind.value,
            to_ref=to_ref,
            relation=relation.value,
        ),
        reason=reason,
    )
    return new_link
