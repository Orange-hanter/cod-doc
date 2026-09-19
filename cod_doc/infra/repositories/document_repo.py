"""Document and Section repositories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from cod_doc.domain.entities import (
    DocNode,
    Document,
    DocumentStatus,
    DocumentType,
    Section,
    Sensitivity,
)
from cod_doc.infra.models import DocNodeModel, DocumentModel, SectionModel
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
            frontmatter_raw=model.frontmatter_raw,
            title_in_body=model.title_in_body,
            content_sha256_head=model.content_sha256_head,
            projection_hash=model.projection_hash,
            node_id=model.node_id,
            node_position=model.node_position,
            created=model.created,
            last_updated=model.last_updated,
            last_reviewed=model.last_reviewed,
        )

    def _to_model(self, entity: Document) -> DocumentModel:
        kwargs: dict[str, Any] = {
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
            "frontmatter_raw": entity.frontmatter_raw,
            "title_in_body": entity.title_in_body,
            "content_sha256_head": entity.content_sha256_head,
            "projection_hash": entity.projection_hash,
            "node_id": entity.node_id,
            "node_position": entity.node_position,
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

    def get_by_path(self, project_id: int, path: str) -> Document | None:
        """Найти документ по пути проекции (`path`), а не по `doc_key`.

        ADO-109: overview знает про MASTER только имя файла на диске
        (`project.master_md`), а страница документа ищет по `doc_key` —
        ссылка «Открыть целиком» вела в 404.
        """
        stmt = select(DocumentModel).where(
            DocumentModel.project_id == project_id,
            DocumentModel.path == path,
        )
        model = self.session.execute(stmt).scalars().first()
        return self._to_domain(model) if model else None

    def list_for_project(self, project_id: int) -> list[Document]:
        stmt = (
            select(DocumentModel)
            .where(DocumentModel.project_id == project_id)
            .order_by(DocumentModel.doc_key)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]


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
        kwargs: dict[str, Any] = {
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


class DocNodeRepository(BaseRepository[DocNode, DocNodeModel]):
    """Разделы дерева документации (ADO-116)."""

    model_cls = DocNodeModel

    def _to_domain(self, model: DocNodeModel) -> DocNode:
        return DocNode(
            row_id=model.row_id,
            project_id=model.project_id,
            node_key=model.node_key,
            parent_id=model.parent_id,
            title=model.title,
            intent=model.intent,
            position=model.position,
            expected_types=list(model.expected_types or []),
            min_docs=model.min_docs,
            is_inbox=bool(model.is_inbox),
        )

    def _to_model(self, entity: DocNode) -> DocNodeModel:
        kwargs: dict[str, Any] = {
            "project_id": entity.project_id,
            "node_key": entity.node_key,
            "parent_id": entity.parent_id,
            "title": entity.title,
            "intent": entity.intent,
            "position": entity.position,
            "expected_types": list(entity.expected_types),
            "min_docs": entity.min_docs,
            "is_inbox": entity.is_inbox,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        return DocNodeModel(**kwargs)

    def get_by_key(self, project_id: int, node_key: str) -> DocNode | None:
        stmt = select(DocNodeModel).where(
            DocNodeModel.project_id == project_id,
            DocNodeModel.node_key == node_key,
        )
        m = self.session.execute(stmt).scalar_one_or_none()
        return self._to_domain(m) if m else None

    def list_for_project(self, project_id: int) -> list[DocNode]:
        """Разделы в порядке показа: position, затем node_key как тай-брейк."""
        stmt = (
            select(DocNodeModel)
            .where(DocNodeModel.project_id == project_id)
            .order_by(DocNodeModel.position, DocNodeModel.node_key)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]

    def inbox(self, project_id: int) -> DocNode | None:
        stmt = select(DocNodeModel).where(
            DocNodeModel.project_id == project_id,
            DocNodeModel.is_inbox.is_(True),
        )
        m = self.session.execute(stmt).scalars().first()
        return self._to_domain(m) if m else None

    def next_position(self, project_id: int) -> int:
        """Следующая позиция в конце списка — агрегатом, а не выгрузкой строк.

        Гонка та же, что у ``StorySectionRepository.next_position`` и с тем же
        последствием: дублирующийся ``position`` деградирует до сортировки по
        ``node_key``, а не до ошибки. ``position`` — advisory-порядок показа.
        """
        stmt = select(func.max(DocNodeModel.position)).where(DocNodeModel.project_id == project_id)
        current = self.session.execute(stmt).scalar()
        return 0 if current is None else int(current) + 1

    def counts_by_node(self, project_id: int) -> dict[int | None, int]:
        """``node_id → число документов``; ключ ``None`` — не разложенные.

        Один агрегат на весь экран: считать документы по разделу отдельным
        запросом на каждый раздел — это N+1 ровно на каждый показ списка.
        """
        stmt = (
            select(DocumentModel.node_id, func.count(DocumentModel.row_id))
            .where(DocumentModel.project_id == project_id)
            .group_by(DocumentModel.node_id)
        )
        return {row[0]: int(row[1]) for row in self.session.execute(stmt)}
