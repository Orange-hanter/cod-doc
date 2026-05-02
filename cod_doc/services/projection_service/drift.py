"""detect_drift — classify DB ↔ projection_hash ↔ on-disk file state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._internals import _require_doc_model
from ._safety import _safe_target, _sha256
from ._types import DriftReport, DriftStatus
from .render import render_markdown

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def detect_drift(
    session: Session,
    document_id: int,
    *,
    root_path: Path,
) -> DriftReport:
    """Classify the drift state between DB, projection_hash, and disk file.

    Returns a `DriftReport` with `status` ∈ `DriftStatus`.
    """
    model = _require_doc_model(session, document_id)
    file_path = _safe_target(root_path, model.path)
    content = render_markdown(session, document_id)
    db_hash = _sha256(content)

    if not file_path.exists():
        return DriftReport(
            document_id=document_id,
            status=DriftStatus.MISSING,
            projection_hash=model.projection_hash,
            db_content_hash=db_hash,
            file_hash=None,
        )

    file_hash = _sha256(file_path.read_text(encoding="utf-8"))

    if model.projection_hash != db_hash:
        status = DriftStatus.STALE_EXPORT
    elif file_hash != model.projection_hash:
        status = DriftStatus.EDITED_IN_PLACE
    else:
        status = DriftStatus.IN_SYNC

    return DriftReport(
        document_id=document_id,
        status=status,
        projection_hash=model.projection_hash,
        db_content_hash=db_hash,
        file_hash=file_hash,
    )
