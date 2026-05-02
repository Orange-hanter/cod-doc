"""export_document — write the rendered projection to disk + update hash."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._internals import _require_doc_model
from ._safety import _safe_target, _sha256
from ._types import ExportResult
from .render import render_markdown

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def export_document(
    session: Session,
    document_id: int,
    *,
    root_path: Path,
    force: bool = False,
    audience: str | None = None,
) -> ExportResult:
    """Write the document projection to disk and update `projection_hash`.

    Idempotent: skips the write if `projection_hash` already matches the current
    DB content (no changes since last export), unless `force=True`.

    `audience` (COD-025 / SD-002) controls redaction in the rendered body —
    see `render_markdown`. The `projection_hash` is only updated for the
    canonical (audience=None) export, so an audience-specific export does NOT
    overwrite the stored hash. This keeps `detect_drift` consistent against
    the canonical projection.

    Returns `ExportResult` with `written=False` on a skipped export.
    """
    model = _require_doc_model(session, document_id)
    target = _safe_target(root_path, model.path)
    content = render_markdown(session, document_id, audience=audience)
    content_hash = _sha256(content)

    if audience is None and not force and model.projection_hash == content_hash:
        return ExportResult(
            document_id=document_id,
            path=target,
            written=False,
            content_hash=content_hash,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    if audience is None:
        model.projection_hash = content_hash
        session.flush()

    return ExportResult(
        document_id=document_id,
        path=target,
        written=True,
        content_hash=content_hash,
    )
