"""RevisionService — append-only history for any entity.

DATA_MODEL §3.5: `revision_id` is a ULID (sortable by time); `parent_revision_id`
chains revisions of the same entity for optimistic concurrency control.

Public API:
- `write` — append a new revision; auto-fills `revision_id` (ULID) and
  `parent_revision_id` (last revision of the same entity, if any).
- `list_for_entity` — full history of an entity, oldest → newest.
- `revert` — undo a revision. Defers to entity-specific services
  (DocService.patch / TaskService.update / …); stubbed until COD-022 wires
  the dispatch table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session
from ulid import ULID

from cod_doc.domain.entities import EntityKind, Revision
from cod_doc.infra.models import RevisionModel


class RevisionConflictError(RuntimeError):
    """Raised when `expected_parent_revision_id` does not match the current head.

    Mirrors DATA_MODEL §3.5: writers may pass the parent ULID they last observed;
    if a concurrent writer landed first, the head moved and we refuse the write.
    """


# Sentinel: caller didn't pass a parent expectation. Distinct from `None`,
# which is a *valid* expectation ("I expect this is the first revision").
# Public so that other services can use it as a default parameter value.
NO_PARENT_CHECK: Final = object()
# Keep the underscore alias for backward compatibility within this module.
_NO_PARENT_CHECK = NO_PARENT_CHECK


def _to_domain(model: RevisionModel) -> Revision:
    return Revision(
        row_id=model.row_id,
        revision_id=model.revision_id,
        project_id=model.project_id,
        entity_kind=EntityKind(model.entity_kind),
        entity_id=model.entity_id,
        parent_revision_id=model.parent_revision_id,
        author=model.author,
        diff=model.diff,
        at=model.at,
        reason=model.reason,
        commit_sha=model.commit_sha,
    )


def _current_head(
    session: Session, entity_kind: EntityKind, entity_id: int
) -> str | None:
    """Latest `revision_id` for the entity, or None if no revisions yet."""
    stmt = (
        select(RevisionModel.revision_id)
        .where(
            RevisionModel.entity_kind == entity_kind.value,
            RevisionModel.entity_id == entity_id,
        )
        .order_by(RevisionModel.at.desc(), RevisionModel.row_id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def write(
    session: Session,
    *,
    project_id: int,
    entity_kind: EntityKind,
    entity_id: int,
    author: str,
    diff: str,
    reason: str | None = None,
    commit_sha: str | None = None,
    expected_parent_revision_id: str | None | object = _NO_PARENT_CHECK,
) -> Revision:
    """Append a revision row.

    `parent_revision_id` is auto-derived from the entity's current head.
    If `expected_parent_revision_id` is passed (including explicit `None` for
    "I expect to be the first writer"), it must equal the current head, or
    `RevisionConflictError` is raised — this is the optimistic concurrency hook.
    """
    head = _current_head(session, entity_kind, entity_id)
    if expected_parent_revision_id is not _NO_PARENT_CHECK and head != expected_parent_revision_id:
        raise RevisionConflictError(
            f"head moved: expected parent={expected_parent_revision_id!r}, "
            f"actual={head!r} for {entity_kind.value} #{entity_id}"
        )

    rid_obj = ULID.from_datetime(datetime.now(timezone.utc))
    model = RevisionModel(
        revision_id=str(rid_obj),
        project_id=project_id,
        entity_kind=entity_kind.value,
        entity_id=entity_id,
        parent_revision_id=head,
        author=author,
        at=rid_obj.datetime,
        diff=diff,
        reason=reason,
        commit_sha=commit_sha,
    )
    session.add(model)
    session.flush()
    return _to_domain(model)


def list_for_entity(
    session: Session, entity_kind: EntityKind, entity_id: int
) -> list[Revision]:
    """Full history for the entity, oldest → newest."""
    stmt = (
        select(RevisionModel)
        .where(
            RevisionModel.entity_kind == entity_kind.value,
            RevisionModel.entity_id == entity_id,
        )
        .order_by(RevisionModel.at.asc(), RevisionModel.row_id.asc())
    )
    return [_to_domain(m) for m in session.execute(stmt).scalars()]


def revert(session: Session, revision_id: str) -> Revision:  # noqa: ARG001
    """Undo a revision via the entity's owning service.

    Wired up in COD-022 (completion-flow + entity-service dispatch). Until then
    callers should patch the entity directly and write a new revision.
    """
    raise NotImplementedError(
        "revert dispatches via entity-owning services (Doc/Task/Plan/Story); "
        "see COD-022"
    )
