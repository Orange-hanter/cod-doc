"""Plan and PlanSection repositories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from cod_doc.domain.entities import Plan, PlanSection
from cod_doc.infra.models import PlanModel, PlanSectionModel
from cod_doc.infra.repositories.base import BaseRepository


class PlanRepository(BaseRepository[Plan, PlanModel]):
    model_cls = PlanModel

    def _to_domain(self, model: PlanModel) -> Plan:
        return Plan(
            row_id=model.row_id,
            project_id=model.project_id,
            scope=model.scope,
            principle=model.principle,
            module_id=model.module_id,
            parent_doc_id=model.parent_doc_id,
            completed_log_id=model.completed_log_id,
            created=model.created,
            last_updated=model.last_updated,
        )

    def _to_model(self, entity: Plan) -> PlanModel:
        kwargs: dict[str, Any] = {
            "project_id": entity.project_id,
            "scope": entity.scope,
            "principle": entity.principle,
            "module_id": entity.module_id,
            "parent_doc_id": entity.parent_doc_id,
            "completed_log_id": entity.completed_log_id,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        if entity.created is not None:
            kwargs["created"] = entity.created
        if entity.last_updated is not None:
            kwargs["last_updated"] = entity.last_updated
        return PlanModel(**kwargs)

    def get_by_scope(self, scope: str) -> Plan | None:
        stmt = select(PlanModel).where(PlanModel.scope == scope)
        model = self.session.execute(stmt).scalar_one_or_none()
        return self._to_domain(model) if model else None


class PlanSectionRepository(BaseRepository[PlanSection, PlanSectionModel]):
    model_cls = PlanSectionModel

    def _to_domain(self, model: PlanSectionModel) -> PlanSection:
        return PlanSection(
            row_id=model.row_id,
            plan_id=model.plan_id,
            letter=model.letter,
            title=model.title,
            slug=model.slug,
            position=model.position,
            doc_id=model.doc_id,
        )

    def _to_model(self, entity: PlanSection) -> PlanSectionModel:
        kwargs: dict[str, Any] = {
            "plan_id": entity.plan_id,
            "letter": entity.letter,
            "title": entity.title,
            "slug": entity.slug,
            "position": entity.position,
            "doc_id": entity.doc_id,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return PlanSectionModel(**kwargs)

    def list_for_plan(self, plan_id: int) -> list[PlanSection]:
        stmt = (
            select(PlanSectionModel)
            .where(PlanSectionModel.plan_id == plan_id)
            .order_by(PlanSectionModel.position)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]
