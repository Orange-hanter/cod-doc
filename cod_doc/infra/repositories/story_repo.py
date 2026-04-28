"""User story / acceptance / link repositories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from cod_doc.domain.entities import (
    Priority,
    StoryAcceptance,
    StoryLink,
    StoryLinkKind,
    StoryRelation,
    UserStory,
    UserStoryStatus,
)
from cod_doc.infra.models import (
    StoryAcceptanceModel,
    StoryLinkModel,
    UserStoryModel,
)
from cod_doc.infra.repositories.base import BaseRepository


class UserStoryRepository(BaseRepository[UserStory, UserStoryModel]):
    model_cls = UserStoryModel

    def _to_domain(self, model: UserStoryModel) -> UserStory:
        return UserStory(
            row_id=model.row_id,
            project_id=model.project_id,
            story_id=model.story_id,
            persona=model.persona,
            narrative=model.narrative,
            status=UserStoryStatus(model.status),
            priority=Priority(model.priority),
            created=model.created,
            last_updated=model.last_updated,
        )

    def _to_model(self, entity: UserStory) -> UserStoryModel:
        kwargs: dict[str, Any] = {
            "project_id": entity.project_id,
            "story_id": entity.story_id,
            "persona": entity.persona,
            "narrative": entity.narrative,
            "status": entity.status.value,
            "priority": entity.priority.value,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        if entity.created is not None:
            kwargs["created"] = entity.created
        if entity.last_updated is not None:
            kwargs["last_updated"] = entity.last_updated
        return UserStoryModel(**kwargs)

    def get_by_story_id(self, story_id: str) -> UserStory | None:
        stmt = select(UserStoryModel).where(UserStoryModel.story_id == story_id)
        m = self.session.execute(stmt).scalar_one_or_none()
        return self._to_domain(m) if m else None

    def list_for_project(self, project_id: int) -> list[UserStory]:
        stmt = (
            select(UserStoryModel)
            .where(UserStoryModel.project_id == project_id)
            .order_by(UserStoryModel.story_id)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]


class StoryAcceptanceRepository(BaseRepository[StoryAcceptance, StoryAcceptanceModel]):
    model_cls = StoryAcceptanceModel

    def _to_domain(self, model: StoryAcceptanceModel) -> StoryAcceptance:
        return StoryAcceptance(
            row_id=model.row_id,
            story_id=model.story_id,
            position=model.position,
            criterion=model.criterion,
            met=bool(model.met),
        )

    def _to_model(self, entity: StoryAcceptance) -> StoryAcceptanceModel:
        kwargs: dict[str, Any] = {
            "story_id": entity.story_id,
            "position": entity.position,
            "criterion": entity.criterion,
            "met": entity.met,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return StoryAcceptanceModel(**kwargs)

    def list_for_story(self, story_id: int) -> list[StoryAcceptance]:
        stmt = (
            select(StoryAcceptanceModel)
            .where(StoryAcceptanceModel.story_id == story_id)
            .order_by(StoryAcceptanceModel.position)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]


class StoryLinkRepository(BaseRepository[StoryLink, StoryLinkModel]):
    model_cls = StoryLinkModel

    def _to_domain(self, model: StoryLinkModel) -> StoryLink:
        return StoryLink(
            row_id=model.row_id,
            story_id=model.story_id,
            to_kind=StoryLinkKind(model.to_kind),
            to_ref=model.to_ref,
            relation=StoryRelation(model.relation),
        )

    def _to_model(self, entity: StoryLink) -> StoryLinkModel:
        kwargs: dict[str, Any] = {
            "story_id": entity.story_id,
            "to_kind": entity.to_kind.value,
            "to_ref": entity.to_ref,
            "relation": entity.relation.value,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return StoryLinkModel(**kwargs)

    def list_for_story(self, story_id: int) -> list[StoryLink]:
        stmt = (
            select(StoryLinkModel)
            .where(StoryLinkModel.story_id == story_id)
            .order_by(StoryLinkModel.row_id)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]
