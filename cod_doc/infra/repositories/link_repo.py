"""Link repository."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from cod_doc.domain.entities import Link, LinkKind
from cod_doc.infra.models import LinkModel, SectionModel
from cod_doc.infra.repositories.base import BaseRepository


class LinkRepository(BaseRepository[Link, LinkModel]):
    model_cls = LinkModel

    def _to_domain(self, model: LinkModel) -> Link:
        return Link(
            row_id=model.row_id,
            project_id=model.project_id,
            from_section_id=model.from_section_id,
            raw=model.raw,
            kind=LinkKind(model.kind),
            to_doc_key=model.to_doc_key,
            to_task_id=model.to_task_id,
            to_story_id=model.to_story_id,
            to_adr_id=model.to_adr_id,
            resolved=bool(model.resolved),
            last_checked=model.last_checked,
            broken_reason=model.broken_reason,
        )

    def _to_model(self, entity: Link) -> LinkModel:
        kwargs: dict[str, Any] = {
            "project_id": entity.project_id,
            "from_section_id": entity.from_section_id,
            "raw": entity.raw,
            "kind": entity.kind.value,
            "to_doc_key": entity.to_doc_key,
            "to_task_id": entity.to_task_id,
            "to_story_id": entity.to_story_id,
            "to_adr_id": entity.to_adr_id,
            "resolved": entity.resolved,
            "last_checked": entity.last_checked,
            "broken_reason": entity.broken_reason,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return LinkModel(**kwargs)

    def list_for_section(self, section_id: int) -> list[Link]:
        stmt = (
            select(LinkModel)
            .where(LinkModel.from_section_id == section_id)
            .order_by(LinkModel.row_id)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]

    def list_for_doc_key(self, project_id: int, doc_key: str) -> list[Link]:
        stmt = select(LinkModel).where(
            LinkModel.project_id == project_id,
            LinkModel.to_doc_key == doc_key,
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]

    def delete_for_section(self, section_id: int) -> None:
        stmt = select(LinkModel).where(LinkModel.from_section_id == section_id)
        for model in self.session.execute(stmt).scalars():
            self.session.delete(model)
        self.session.flush()

    def incoming_counts(self, project_id: int) -> dict[str, int]:
        """``doc_key → сколько документов на него ссылается``.

        Один агрегат на весь список. Документ без входящих ссылок — сигнал, что
        он выпал из навигации: на живом корпусе cod-doc таких 55 из 170, и
        увидеть это можно было только отдельным запросом к БД.
        """
        stmt = (
            select(LinkModel.to_doc_key, func.count(LinkModel.row_id))
            .where(
                LinkModel.project_id == project_id,
                LinkModel.to_doc_key.is_not(None),
            )
            .group_by(LinkModel.to_doc_key)
        )
        return {row[0]: int(row[1]) for row in self.session.execute(stmt) if row[0]}

    def outgoing_counts(self, project_id: int) -> dict[int, int]:
        """``document_id → сколько ссылок уходит из его секций``.

        Источник ссылки — секция, а не документ, поэтому счёт идёт через join
        и сворачивается до документа здесь, а не в вызывающем.
        """
        stmt = (
            select(SectionModel.document_id, func.count(LinkModel.row_id))
            .join(LinkModel, LinkModel.from_section_id == SectionModel.row_id)
            .where(LinkModel.project_id == project_id)
            .group_by(SectionModel.document_id)
        )
        return {int(row[0]): int(row[1]) for row in self.session.execute(stmt)}
