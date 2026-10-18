"""Module-internal helpers — story lookup + JSON-diff fragment formatter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import UserStoryModel
from cod_doc.services.sync_service import leases

from ._types import StoryNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _require_story(session: Session, story_id: str, *, project_id: int) -> UserStoryModel:
    # STO-023: story_id уникален в проекте (0047), не во всей БД.
    stmt = select(UserStoryModel).where(
        UserStoryModel.project_id == project_id, UserStoryModel.story_id == story_id
    )
    m = session.execute(stmt).scalar_one_or_none()
    if m is None:
        # CLO-030: провизорный ID, закреплённый push'ем, резолвится алиасом.
        current = leases.current_id(session, project_id, "user_story", story_id)
        if current is not None:
            return _require_story(session, current, project_id=project_id)
        raise StoryNotFoundError(story_id)
    return m


def _diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields}, ensure_ascii=False)
