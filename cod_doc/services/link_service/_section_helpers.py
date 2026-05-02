"""DB-bound common helpers — section/document lookups + project_id resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.infra.models import DocumentModel, LinkModel, SectionModel
from cod_doc.services import doc_service as docs

from ._types import LinkNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _section_or_raise(session: Session, section_id: int) -> SectionModel:
    model = session.get(SectionModel, section_id)
    if model is None:
        raise docs.SectionNotFoundError(f"section #{section_id}")
    return model


def _project_id_for_section(session: Session, section_id: int) -> int:
    sec = _section_or_raise(session, section_id)
    doc = session.get(DocumentModel, sec.document_id)
    assert doc is not None
    return doc.project_id


def _link_or_raise(session: Session, link_row_id: int) -> LinkModel:
    m = session.get(LinkModel, link_row_id)
    if m is None:
        raise LinkNotFoundError(f"link #{link_row_id}")
    return m
