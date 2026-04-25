"""Document and Section repositories."""

from __future__ import annotations

from sqlalchemy import select

from cod_doc.domain.entities import (
    Document,
    DocumentStatus,
    DocumentType,
    Section,
    Sensitivity,
)
from cod_doc.infra.models import DocumentModel, SectionModel
from cod_doc.infra.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document, DocumentModel]):
    model_cls = DocumentModel

    def _to_domain(self, model: DocumentModel) -> Document:
        return Document(
            row_id=model.row_id,
            project_id=model.project_id,
            doc_key=model.doc_key,
            path=model.path,
            type=DocumentType(model.type),
            status=DocumentStatus(model.status),
            source_of_truth=bool(model.source_of_truth),
            sensitivity=Sensitivity(model.sensitivity),
            owner=model.owner,
            title=model.title,
            preamble=model.preamble,
            frontmatter=dict(model.frontmatter_json or {}),
            projection_hash=model.projection_hash,
            created=model.created,
            last_updated=model.last_updated,
            last_reviewed=model.last_reviewed,
        )

    def _to_model(self, entity: Document) -> DocumentModel:
        kwargs: dict = {
            "project_id": entity.project_id,
            "doc_key": entity.doc_key,
            "path": entity.path,
            "type": entity.type.value,
            "status": entity.status.value,
            "source_of_truth": entity.source_of_truth,
            "sensitivity": entity.sensitivity.value,
            "owner": entity.owner,
            "title": entity.title,
            "preamble": entity.preamble,
            "frontmatter_json": entity.frontmatter,
            "projection_hash": entity.projection_hash,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        if entity.created is not None:
            kwargs["created"] = entity.created
        if entity.last_updated is not None:
            kwargs["last_updated"] = entity.last_updated
        if entity.last_reviewed is not None:
            kwargs["last_reviewed"] = entity.last_reviewed
        return DocumentModel(**kwargs)

    def get_by_key(self, project_id: int, doc_key: str) -> Document | None:
        stmt = select(DocumentModel).where(
            DocumentModel.project_id == project_id,
            DocumentModel.doc_key == doc_key,
        )
        model = self.session.execute(stmt).scalar_one_or_none()
        return self._to_domain(model) if model else None


class SectionRepository(BaseRepository[Section, SectionModel]):
    model_cls = SectionModel

    def _to_domain(self, model: SectionModel) -> Section:
        return Section(
            row_id=model.row_id,
            document_id=model.document_id,
            anchor=model.anchor,
            heading=model.heading,
            level=model.level,
            position=model.position,
            body=model.body,
            content_hash=model.content_hash,
        )

    def _to_model(self, entity: Section) -> SectionModel:
        kwargs: dict = {
            "document_id": entity.document_id,
            "anchor": entity.anchor,
            "heading": entity.heading,
            "level": entity.level,
            "position": entity.position,
            "body": entity.body,
            "content_hash": entity.content_hash,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return SectionModel(**kwargs)

    def list_for_document(self, document_id: int) -> list[Section]:
        stmt = (
            select(SectionModel)
            .where(SectionModel.document_id == document_id)
            .order_by(SectionModel.position)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]
