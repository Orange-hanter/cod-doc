"""Story CRUD + status update + listing reads."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

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

from ._internals import _diff, _require_story
from ._types import StoryAlreadyExistsError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
    if (
        session.execute(
            select(UserStoryModel.row_id).where(UserStoryModel.story_id == story_id)
        ).scalar_one_or_none()
        is not None
    ):
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
            "create",
            story_id=story_id,
            status=status.value,
            acceptance_count=len(acceptance or []),
        ),
        reason=reason or "create",
    )
    return story


def get(session: Session, story_id: str) -> UserStory | None:
    return UserStoryRepository(session).get_by_story_id(story_id)


def list_for_project(session: Session, project_id: int) -> list[UserStory]:
    return UserStoryRepository(session).list_for_project(project_id)


def next_story_id(session: Session, project_id: int, prefix: str = "US") -> str:
    """Auto-numbered story_id (e.g. ``US-007``) for a project + prefix.

    Used by AI-driven story generation flows where the LLM proposes drafts
    and the route persists them with sequential ids.
    """
    rows = session.execute(
        select(UserStoryModel.story_id).where(
            UserStoryModel.project_id == project_id,
            UserStoryModel.story_id.like(f"{prefix}-%"),
        )
    ).scalars()
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    max_n = 0
    for sid in rows:
        m = pat.match(sid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}-{max_n + 1:03d}"


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
