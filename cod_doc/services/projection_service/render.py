"""render_markdown — pure projection: frontmatter + body, no I/O."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.services import doc_service as docs

from ._frontmatter import _frontmatter_dict, _render_frontmatter
from ._internals import _require_doc_model
from ._redaction import _REDACTION_MARKER, _audience_blocks_sensitivity

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def render_markdown(
    session: Session,
    document_id: int,
    *,
    audience: str | None = None,
) -> str:
    """Render the full document markdown: frontmatter + body.

    Uses `DocService.render_body` for the body (reads the `document_body` view).
    Does NOT modify the DB. Raises `DocumentNotFoundError` if unknown.

    `audience` (COD-025 / SD-002) gates content by `document.sensitivity`.
    When the audience cannot see the document's level, the body is replaced
    with a single redaction marker line. The frontmatter still indicates the
    sensitivity so consumers know why content is missing. `None` (default)
    means owner-level access — no redaction is applied.
    """
    model = _require_doc_model(session, document_id)
    fm = _frontmatter_dict(model)
    frontmatter_block = _render_frontmatter(fm)
    if _audience_blocks_sensitivity(audience, model.sensitivity):
        body = _REDACTION_MARKER.format(sensitivity=model.sensitivity) + "\n"
    else:
        body = docs.render_body(session, document_id) or ""
    return frontmatter_block + body
