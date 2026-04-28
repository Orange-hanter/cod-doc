"""StoryService — write/read paths for user stories.

COD-014. Implements [capability/user-stories-graph.md](../../docs/system/capabilities/user-stories-graph.md).

Public API:
- `create` — story + optional initial acceptance criteria; STORY revision.
- `get` / `list_for_project` / `list_acceptance` / `list_links` / `list_tasks`.
- `update_status` — set story.status; supports optimistic concurrency.
- `add_criterion` / `set_criterion_met` — manage acceptance criteria.
- `link` — attach story to task / document / module with a relation kind;
  hard-errors on broken refs (per [document-link.md §4](../../docs/system/standards/document-link.md))
  and de-dupes existing edges.
- `coverage` — derived `CoverageStatus` from acceptance + linked task progress.

All mutations use `entity_kind=STORY` for revisions (per
[standards/revision-history.md](../../docs/system/standards/revision-history.md));
diffs are JSON-patch fragments with an `op` discriminator.

Caller owns the transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from cod_doc.domain.entities import (
    EntityKind,
    Priority,
    StoryAcceptance,
    StoryLink,
    StoryLinkKind,
    StoryRelation,
    Task,
    UserStory,
    UserStoryStatus,
)
from cod_doc.infra.models import (
    DocumentModel,
    ModuleModel,
    StoryAcceptanceModel,
    StoryLinkModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.infra.repositories import (
    StoryAcceptanceRepository,
    StoryLinkRepository,
    TaskRepository,
    UserStoryRepository,
)
from cod_doc.services import revision_service as rev
from cod_doc.services import validation


class StoryNotFoundError(LookupError):
    pass


class StoryAlreadyExistsError(ValueError):
    pass


class AcceptanceNotFoundError(LookupError):
    pass


class BrokenLinkError(ValueError):
    """Raised when `link()` target doesn't resolve in the current project."""


class CoverageStatus(str, Enum):
    """Derived coverage state. See [user-stories-graph.md §4]."""

    DRAFT = "draft"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in-progress"
    DELIVERED = "delivered"
    DEFERRED = "deferred"


@dataclass(slots=True)
class StoryCoverage:
    story_id: str
    status: CoverageStatus
    tasks_total: int
    tasks_done: int
    tasks_in_progress: int
    acceptance_total: int
    acceptance_met: int


# --------------------------------------------------------------------------- #
# Internals                                                                     #
# --------------------------------------------------------------------------- #


def _require_story(session: Session, story_id: str) -> UserStoryModel:
    stmt = select(UserStoryModel).where(UserStoryModel.story_id == story_id)
    m = session.execute(stmt).scalar_one_or_none()
    if m is None:
        raise StoryNotFoundError(story_id)
    return m


def _diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields})


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


# --------------------------------------------------------------------------- #
# create / get / list                                                           #
# --------------------------------------------------------------------------- #


def create(
    session: Session,
    *,
    project_id: int,
    story_id: str,
    persona: str,
    narrative: str,
    priority: Priority,
    author: str,
    status: UserStoryStatus = UserStoryStatus.DRAFT,
    acceptance: list[str] | None = None,
    reason: str | None = None,
) -> UserStory:
    """Persist a story (+ optional acceptance criteria) and write its initial revision."""
    validation.validate_story_id(story_id)
    if session.execute(
        select(UserStoryModel.row_id).where(UserStoryModel.story_id == story_id)
    ).scalar_one_or_none() is not None:
        raise StoryAlreadyExistsError(story_id)

    now = datetime.now(UTC)
    story = UserStoryRepository(session).add(
        UserStory(
            project_id=project_id,
            story_id=story_id,
            persona=persona,
            narrative=narrative,
            status=status,
            priority=priority,
            created=now,
            last_updated=now,
        )
    )
    assert story.row_id is not None

    if acceptance:
        for i, criterion in enumerate(acceptance):
            session.add(
                StoryAcceptanceModel(
                    story_id=story.row_id,
                    position=i,
                    criterion=criterion,
                    met=False,
                )
            )
        session.flush()

    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.STORY,
        entity_id=story.row_id,
        author=author,
        diff=_diff(
            "create", story_id=story_id, status=status.value,
            acceptance_count=len(acceptance or []),
        ),
        reason=reason or "create",
    )
    return story


def get(session: Session, story_id: str) -> UserStory | None:
    return UserStoryRepository(session).get_by_story_id(story_id)


def list_for_project(session: Session, project_id: int) -> list[UserStory]:
    return UserStoryRepository(session).list_for_project(project_id)


def list_acceptance(session: Session, story_id: str) -> list[StoryAcceptance]:
    model = _require_story(session, story_id)
    return StoryAcceptanceRepository(session).list_for_story(model.row_id)


def list_links(session: Session, story_id: str) -> list[StoryLink]:
    model = _require_story(session, story_id)
    return StoryLinkRepository(session).list_for_story(model.row_id)


def list_tasks(session: Session, story_id: str) -> list[Task]:
    """Tasks linked to the story via `implemented_by` relation."""
    model = _require_story(session, story_id)
    stmt = (
        select(TaskModel)
        .join(StoryLinkModel, StoryLinkModel.to_ref == TaskModel.task_id)
        .where(
            StoryLinkModel.story_id == model.row_id,
            StoryLinkModel.to_kind == StoryLinkKind.TASK.value,
            StoryLinkModel.relation == StoryRelation.IMPLEMENTED_BY.value,
            TaskModel.project_id == model.project_id,
        )
        .order_by(TaskModel.task_id)
    )
    repo = TaskRepository(session)
    return [repo._to_domain(m) for m in session.execute(stmt).scalars()]


# --------------------------------------------------------------------------- #
# update_status                                                                 #
# --------------------------------------------------------------------------- #


def update_status(
    session: Session,
    *,
    story_id: str,
    new_status: UserStoryStatus,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | None | object = rev.NO_PARENT_CHECK,
) -> UserStory:
    model = _require_story(session, story_id)
    old_status = model.status
    if old_status == new_status.value:
        s = UserStoryRepository(session).get_by_story_id(story_id)
        assert s is not None
        return s

    model.status = new_status.value
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.STORY,
        entity_id=model.row_id,
        author=author,
        diff=_diff("status", old=old_status, new=new_status.value),
        reason=reason,
        expected_parent_revision_id=expected_parent_revision_id,
    )
    s = UserStoryRepository(session).get(model.row_id)
    assert s is not None
    return s


# --------------------------------------------------------------------------- #
# acceptance criteria                                                          #
# --------------------------------------------------------------------------- #


def add_criterion(
    session: Session,
    *,
    story_id: str,
    criterion: str,
    author: str,
    reason: str | None = None,
) -> StoryAcceptance:
    model = _require_story(session, story_id)
    repo = StoryAcceptanceRepository(session)
    existing = repo.list_for_story(model.row_id)
    next_pos = (max((a.position for a in existing), default=-1)) + 1

    ac = repo.add(
        StoryAcceptance(
            story_id=model.row_id, position=next_pos, criterion=criterion, met=False
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
        diff=_diff("add_criterion", position=next_pos, criterion=criterion),
        reason=reason,
    )
    return ac


def set_criterion_met(
    session: Session,
    *,
    story_id: str,
    position: int,
    met: bool,
    author: str,
    reason: str | None = None,
) -> StoryAcceptance:
    model = _require_story(session, story_id)
    stmt = select(StoryAcceptanceModel).where(
        StoryAcceptanceModel.story_id == model.row_id,
        StoryAcceptanceModel.position == position,
    )
    ac_model = session.execute(stmt).scalar_one_or_none()
    if ac_model is None:
        raise AcceptanceNotFoundError(f"{story_id} position={position}")

    if bool(ac_model.met) == met:
        # Idempotent — return without writing a revision.
        return StoryAcceptanceRepository(session)._to_domain(ac_model)

    old_met = bool(ac_model.met)
    ac_model.met = met
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.STORY,
        entity_id=model.row_id,
        author=author,
        diff=_diff("criterion_met", position=position, old=old_met, new=met),
        reason=reason,
    )
    return StoryAcceptanceRepository(session)._to_domain(ac_model)


# --------------------------------------------------------------------------- #
# link                                                                          #
# --------------------------------------------------------------------------- #


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
            story_id=model.row_id, to_kind=to_kind, to_ref=to_ref, relation=relation,
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


# --------------------------------------------------------------------------- #
# coverage                                                                      #
# --------------------------------------------------------------------------- #


def coverage(session: Session, story_id: str) -> StoryCoverage:
    """Derive a coverage snapshot for a story. See [user-stories-graph.md §4]."""
    model = _require_story(session, story_id)

    acceptance = StoryAcceptanceRepository(session).list_for_story(model.row_id)
    acceptance_total = len(acceptance)
    acceptance_met = sum(1 for a in acceptance if a.met)

    impl_tasks = list_tasks(session, story_id)
    tasks_total = len(impl_tasks)
    tasks_done = sum(1 for t in impl_tasks if t.status.value == "done")
    tasks_in_progress = sum(1 for t in impl_tasks if t.status.value == "in-progress")

    pinned = {UserStoryStatus.DRAFT, UserStoryStatus.DEFERRED}
    if UserStoryStatus(model.status) in pinned:
        derived = CoverageStatus(model.status)
    elif (
        tasks_total > 0
        and tasks_done == tasks_total
        and acceptance_met == acceptance_total
    ):
        derived = CoverageStatus.DELIVERED
    elif tasks_done > 0 or tasks_in_progress > 0:
        derived = CoverageStatus.IN_PROGRESS
    else:
        # Either no impl tasks, or all impl tasks pending and acceptance not all met.
        derived = CoverageStatus.ACCEPTED

    return StoryCoverage(
        story_id=story_id,
        status=derived,
        tasks_total=tasks_total,
        tasks_done=tasks_done,
        tasks_in_progress=tasks_in_progress,
        acceptance_total=acceptance_total,
        acceptance_met=acceptance_met,
    )


__all__ = [
    "AcceptanceNotFoundError",
    "BrokenLinkError",
    "CoverageStatus",
    "StoryAlreadyExistsError",
    "StoryCoverage",
    "StoryNotFoundError",
    "add_criterion",
    "coverage",
    "create",
    "get",
    "link",
    "list_acceptance",
    "list_for_project",
    "list_links",
    "list_tasks",
    "set_criterion_met",
    "update_status",
]
