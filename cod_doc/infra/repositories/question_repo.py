"""OpenQuestion / option / link repositories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from cod_doc.domain.entities import (
    OpenQuestion,
    Priority,
    QuestionLink,
    QuestionLinkKind,
    QuestionOption,
    QuestionRelation,
    QuestionStatus,
)
from cod_doc.infra.models import (
    OpenQuestionModel,
    QuestionLinkModel,
    QuestionOptionModel,
)
from cod_doc.infra.repositories.base import BaseRepository


class OpenQuestionRepository(BaseRepository[OpenQuestion, OpenQuestionModel]):
    model_cls = OpenQuestionModel

    def _to_domain(self, model: OpenQuestionModel) -> OpenQuestion:
        return OpenQuestion(
            row_id=model.row_id,
            project_id=model.project_id,
            question_id=model.question_id,
            title=model.title,
            question=model.question,
            context=model.context,
            status=QuestionStatus(model.status),
            priority=Priority(model.priority),
            owner=model.owner,
            resolution=model.resolution,
            resolved_by_adr=model.resolved_by_adr,
            resolved_at=model.resolved_at,
            source_doc_key=model.source_doc_key,
            author=model.author,
            created=model.created,
            last_updated=model.last_updated,
        )

    def _to_model(self, entity: OpenQuestion) -> OpenQuestionModel:
        kwargs: dict[str, Any] = {
            "project_id": entity.project_id,
            "question_id": entity.question_id,
            "title": entity.title,
            "question": entity.question,
            "context": entity.context,
            "status": entity.status.value,
            "priority": entity.priority.value,
            "owner": entity.owner,
            "resolution": entity.resolution,
            "resolved_by_adr": entity.resolved_by_adr,
            "resolved_at": entity.resolved_at,
            "source_doc_key": entity.source_doc_key,
            "author": entity.author,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        if entity.created is not None:
            kwargs["created"] = entity.created
        if entity.last_updated is not None:
            kwargs["last_updated"] = entity.last_updated
        return OpenQuestionModel(**kwargs)

    def get_by_question_id(self, project_id: int, question_id: str) -> OpenQuestion | None:
        stmt = select(OpenQuestionModel).where(
            OpenQuestionModel.project_id == project_id,
            OpenQuestionModel.question_id == question_id,
        )
        m = self.session.execute(stmt).scalar_one_or_none()
        return self._to_domain(m) if m else None


class QuestionOptionRepository(BaseRepository[QuestionOption, QuestionOptionModel]):
    model_cls = QuestionOptionModel

    def _to_domain(self, model: QuestionOptionModel) -> QuestionOption:
        return QuestionOption(
            row_id=model.row_id,
            question_row_id=model.question_row_id,
            position=model.position,
            title=model.title,
            body=model.body,
            chosen=model.chosen,
        )

    def _to_model(self, entity: QuestionOption) -> QuestionOptionModel:
        kwargs: dict[str, Any] = {
            "question_row_id": entity.question_row_id,
            "position": entity.position,
            "title": entity.title,
            "body": entity.body,
            "chosen": entity.chosen,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return QuestionOptionModel(**kwargs)

    def list_for_question(self, question_row_id: int) -> list[QuestionOption]:
        stmt = (
            select(QuestionOptionModel)
            .where(QuestionOptionModel.question_row_id == question_row_id)
            .order_by(QuestionOptionModel.position)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]


class QuestionLinkRepository(BaseRepository[QuestionLink, QuestionLinkModel]):
    model_cls = QuestionLinkModel

    def _to_domain(self, model: QuestionLinkModel) -> QuestionLink:
        return QuestionLink(
            row_id=model.row_id,
            question_row_id=model.question_row_id,
            to_kind=QuestionLinkKind(model.to_kind),
            to_ref=model.to_ref,
            relation=QuestionRelation(model.relation),
            note=model.note,
            resolved=model.resolved,
            broken_reason=model.broken_reason,
            last_checked=model.last_checked,
        )

    def _to_model(self, entity: QuestionLink) -> QuestionLinkModel:
        kwargs: dict[str, Any] = {
            "question_row_id": entity.question_row_id,
            "to_kind": entity.to_kind.value,
            "to_ref": entity.to_ref,
            "relation": entity.relation.value,
            "note": entity.note,
            "resolved": entity.resolved,
            "broken_reason": entity.broken_reason,
            "last_checked": entity.last_checked,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return QuestionLinkModel(**kwargs)

    def list_for_question(self, question_row_id: int) -> list[QuestionLink]:
        stmt = (
            select(QuestionLinkModel)
            .where(QuestionLinkModel.question_row_id == question_row_id)
            .order_by(QuestionLinkModel.row_id)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]
