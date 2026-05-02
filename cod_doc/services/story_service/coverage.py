"""Derived coverage status — combines acceptance + linked task progress."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.domain.entities import UserStoryStatus
from cod_doc.infra.repositories import StoryAcceptanceRepository

from ._internals import _require_story
from ._types import CoverageStatus, StoryCoverage
from .crud import list_tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
    elif tasks_total > 0 and tasks_done == tasks_total and acceptance_met == acceptance_total:
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
