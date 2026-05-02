"""import_document — read a projection file, apply frontmatter changes back to DB.

Module name `import_doc` (not `import`) — Python keyword conflict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import DocumentModel
from cod_doc.infra.repositories import DocumentRepository

from ._frontmatter import _apply_frontmatter_to_model, _parse_frontmatter
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
    """Read a projection file and apply frontmatter changes to the DB record.

    If the file's hash matches `projection_hash`, there is nothing to import
    (returns the existing Document unchanged).

    If no Document with this path exists in the project, returns `None`.

    Full section-body import (parsing markdown sections and updating the DB) is
    handled by the Restate importer (COD-051) which builds on the frontmatter
    parser (COD-050). This function handles the *structural* frontmatter fields
    only (type, status, owner, sensitivity, source_of_truth).
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
    if file_hash == model.projection_hash:
        return DocumentRepository(session)._to_domain(model)

    fm = _parse_frontmatter(content)
    _apply_frontmatter_to_model(model, fm)
    session.flush()
    return DocumentRepository(session)._to_domain(model)
