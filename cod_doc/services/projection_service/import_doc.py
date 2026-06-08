"""import_document — read a projection file, apply markdown changes back to DB.

Module name `import_doc` (not `import`) — Python keyword conflict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import DocumentModel
from cod_doc.infra.repositories import DocumentRepository

from ._safety import _sha256

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Document


def import_document(
    session: Session,
    project_id: int,
    file_path: Path,
    *,
    author: str,
    root_path: Path,
) -> Document | None:
    """Read a projection file and apply its markdown/frontmatter to the DB.

    If no Document with this path exists in the project, returns `None`.

    The accepted file hash is stored as `content_sha256_head`, while
    `projection_hash` is updated to the DB-rendered markdown hash after import.
    This keeps drift detection honest even before renderer output is perfectly
    byte-for-byte round-trip-compatible with human-authored markdown.
    """
    rel_path = str(file_path.relative_to(root_path))
    stmt = select(DocumentModel).where(
        DocumentModel.project_id == project_id,
        DocumentModel.path == rel_path,
    )
    model = session.execute(stmt).scalar_one_or_none()
    if model is None:
        return None

    content = file_path.read_text(encoding="utf-8")
    file_hash = _sha256(content)

    from cod_doc.services import import_service

    doc, _created = import_service.import_or_update_markdown(
        session,
        project_id=project_id,
        doc_key=model.doc_key,
        raw_markdown=content,
        fallback_title=model.title,
        author=author,
        reason="projection import",
        source_sha256=file_hash,
    )
    session.flush()
    assert doc.row_id is not None
    refreshed = session.get(DocumentModel, doc.row_id)
    assert refreshed is not None
    return DocumentRepository(session)._to_domain(refreshed)
