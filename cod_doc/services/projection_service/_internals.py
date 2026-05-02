"""Internal helpers shared by render/export/drift/import modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.infra.models import DocumentModel
from cod_doc.services import doc_service as docs

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _require_doc_model(session: Session, document_id: int) -> DocumentModel:
    m = session.get(DocumentModel, document_id)
    if m is None:
        raise docs.DocumentNotFoundError(f"document #{document_id}")
    return m
