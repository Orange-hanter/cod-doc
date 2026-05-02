"""Acceptance criteria — append + toggle met."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, StoryAcceptance
from cod_doc.infra.models import StoryAcceptanceModel
from cod_doc.infra.repositories import StoryAcceptanceRepository
from cod_doc.services import revision_service as rev

from ._internals import _diff, _require_story
from ._types import AcceptanceNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
        StoryAcceptance(story_id=model.row_id, position=next_pos, criterion=criterion, met=False)
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
