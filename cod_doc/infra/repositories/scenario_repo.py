"""Scenario / step / link repositories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from cod_doc.domain.entities import (
    Scenario,
    ScenarioKind,
    ScenarioLink,
    ScenarioLinkKind,
    ScenarioProvenance,
    ScenarioRelation,
    ScenarioStatus,
    ScenarioStep,
)
from cod_doc.infra.models import (
    ScenarioLinkModel,
    ScenarioModel,
    ScenarioStepModel,
)
from cod_doc.infra.repositories.base import BaseRepository


class ScenarioRepository(BaseRepository[Scenario, ScenarioModel]):
    model_cls = ScenarioModel

    def _to_domain(self, model: ScenarioModel) -> Scenario:
        return Scenario(
            row_id=model.row_id,
            project_id=model.project_id,
            scenario_id=model.scenario_id,
            title=model.title,
            kind=ScenarioKind(model.kind),
            group_key=model.group_key,
            document_id=model.document_id,
            doc_key=model.doc_key,
            section_anchor=model.section_anchor,
            doc_content_hash=model.doc_content_hash,
            module_id=model.module_id,
            subject_ref=model.subject_ref,
            status=ScenarioStatus(model.status),
            provenance=ScenarioProvenance(model.provenance),
            preconditions=model.preconditions,
            expected=model.expected,
            notes=model.notes,
            position=model.position,
            author=model.author,
            created=model.created,
            last_updated=model.last_updated,
        )

    def _to_model(self, entity: Scenario) -> ScenarioModel:
        kwargs: dict[str, Any] = {
            "project_id": entity.project_id,
            "scenario_id": entity.scenario_id,
            "title": entity.title,
            "kind": entity.kind.value,
            "group_key": entity.group_key,
            "document_id": entity.document_id,
            "doc_key": entity.doc_key,
            "section_anchor": entity.section_anchor,
            "doc_content_hash": entity.doc_content_hash,
            "module_id": entity.module_id,
            "subject_ref": entity.subject_ref,
            "status": entity.status.value,
            "provenance": entity.provenance.value,
            "preconditions": entity.preconditions,
            "expected": entity.expected,
            "notes": entity.notes,
            "position": entity.position,
            "author": entity.author,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        if entity.created is not None:
            kwargs["created"] = entity.created
        if entity.last_updated is not None:
            kwargs["last_updated"] = entity.last_updated
        return ScenarioModel(**kwargs)

    def get_by_scenario_id(self, project_id: int, scenario_id: str) -> Scenario | None:
        stmt = select(ScenarioModel).where(
            ScenarioModel.project_id == project_id,
            ScenarioModel.scenario_id == scenario_id,
        )
        m = self.session.execute(stmt).scalar_one_or_none()
        return self._to_domain(m) if m else None

    def list_for_project(self, project_id: int) -> list[Scenario]:
        stmt = (
            select(ScenarioModel)
            .where(ScenarioModel.project_id == project_id)
            .order_by(ScenarioModel.group_key, ScenarioModel.position, ScenarioModel.scenario_id)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]

    def list_for_group(self, project_id: int, group_key: str) -> list[Scenario]:
        stmt = (
            select(ScenarioModel)
            .where(
                ScenarioModel.project_id == project_id,
                ScenarioModel.group_key == group_key,
            )
            .order_by(ScenarioModel.position, ScenarioModel.scenario_id)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]

    def next_position(self, project_id: int, group_key: str) -> int:
        stmt = select(func.max(ScenarioModel.position)).where(
            ScenarioModel.project_id == project_id,
            ScenarioModel.group_key == group_key,
        )
        current = self.session.execute(stmt).scalar_one_or_none()
        return 0 if current is None else int(current) + 1


class ScenarioStepRepository(BaseRepository[ScenarioStep, ScenarioStepModel]):
    model_cls = ScenarioStepModel

    def _to_domain(self, model: ScenarioStepModel) -> ScenarioStep:
        return ScenarioStep(
            row_id=model.row_id,
            scenario_row_id=model.scenario_row_id,
            position=model.position,
            text=model.text,
        )

    def _to_model(self, entity: ScenarioStep) -> ScenarioStepModel:
        kwargs: dict[str, Any] = {
            "scenario_row_id": entity.scenario_row_id,
            "position": entity.position,
            "text": entity.text,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return ScenarioStepModel(**kwargs)

    def list_for_scenario(self, scenario_row_id: int) -> list[ScenarioStep]:
        stmt = (
            select(ScenarioStepModel)
            .where(ScenarioStepModel.scenario_row_id == scenario_row_id)
            .order_by(ScenarioStepModel.position)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]


class ScenarioLinkRepository(BaseRepository[ScenarioLink, ScenarioLinkModel]):
    model_cls = ScenarioLinkModel

    def _to_domain(self, model: ScenarioLinkModel) -> ScenarioLink:
        return ScenarioLink(
            row_id=model.row_id,
            scenario_row_id=model.scenario_row_id,
            to_kind=ScenarioLinkKind(model.to_kind),
            to_ref=model.to_ref,
            relation=ScenarioRelation(model.relation),
        )

    def _to_model(self, entity: ScenarioLink) -> ScenarioLinkModel:
        kwargs: dict[str, Any] = {
            "scenario_row_id": entity.scenario_row_id,
            "to_kind": entity.to_kind.value,
            "to_ref": entity.to_ref,
            "relation": entity.relation.value,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return ScenarioLinkModel(**kwargs)

    def list_for_scenario(self, scenario_row_id: int) -> list[ScenarioLink]:
        stmt = (
            select(ScenarioLinkModel)
            .where(ScenarioLinkModel.scenario_row_id == scenario_row_id)
            .order_by(ScenarioLinkModel.row_id)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]
