"""render_markdown — pure projection: frontmatter + H1 + body, no I/O."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.services import doc_service as docs

from ._frontmatter import _render_frontmatter_block
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
    """Render the full document markdown: frontmatter + H1 + body.

    Uses `DocService.render_body` for the body (reads the `document_body` view).
    Does NOT modify the DB. Raises `DocumentNotFoundError` if unknown.

    ADO-010 (F7): the H1 is re-emitted from `document.title` — the importer
    consumes `# Title` into that column, so without this every export dropped
    the document's heading. A source that had no H1 (`title_in_body=False`)
    does not get one invented. The output always ends with exactly one newline.

    `audience` (COD-025 / SD-002) gates content by `document.sensitivity`.
    When the audience cannot see the document's level, the body is replaced
    with a single redaction marker line. The frontmatter still indicates the
    sensitivity so consumers know why content is missing. `None` (default)
    means owner-level access — no redaction is applied.
    """
    model = _require_doc_model(session, document_id)
    frontmatter_block = _render_frontmatter_block(model)
    if _audience_blocks_sensitivity(audience, model.sensitivity):
        body = _REDACTION_MARKER.format(sensitivity=model.sensitivity)
    else:
        body = docs.render_body(session, document_id) or ""

    show_h1 = bool(model.title) and model.title_in_body is not False
    blocks = [b for b in (f"# {model.title}" if show_h1 else "", body.strip("\n")) if b]
    if not blocks:
        return frontmatter_block
    # One blank line between the closing `---` and the document — the form all
    # 67 frontmatter-carrying docs in this repo use, and what the importer
    # swallows when it splits the frontmatter off.
    prefix = frontmatter_block + "\n" if frontmatter_block else ""
    return prefix + "\n\n".join(blocks) + "\n"
