"""backfill_projection_fidelity — recover the ADO-010 shape columns from disk.

ADO-022. Rows created before migration `0025_projection_fidelity` carry
`frontmatter_raw = NULL` and `title_in_body = NULL`. The renderer reads that
pair as "DB-authored, nothing to preserve" and rebuilds the frontmatter from
`frontmatter_json` — reordering keys, inventing a `type`/`status`/`owner` block
for a file that had none, and adding an `# H1` the source never carried.

This is the cure the export guard points at. It is deliberately *not* an
alembic data migration: the project root lives in another table, files may be
absent, and a downgrade could not restore what it overwrote.

It is also deliberately not `import_document`: that applies the file's
frontmatter back to the DB, silently reverting metadata changes made in the DB
but not yet exported (a `doc accept`, say). Here only the two projection-shape
columns are touched — no domain fields, no revisions, no activity events —
which is the same contract as `import_service._set_projection_shape`. What the
projection does with a recovered block is then decided by `_raw_matches_db`:
verbatim while the DB still agrees with it, re-serialised once it diverges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from cod_doc.infra.models import DocumentModel

from ._frontmatter import _leading_shape
from ._safety import _safe_target, _sha256
from ._types import FidelityBackfillAction, FidelityBackfillItem, FidelityBackfillReport

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def backfill_projection_fidelity(
    session: Session,
    project_id: int,
    *,
    root_path: Path,
    dry_run: bool = False,
) -> FidelityBackfillReport:
    """Fill NULL `frontmatter_raw` / `title_in_body` from the files on disk.

    Scans only the rows that are missing either column, so a repeat run is a
    cheap no-op (`filled == 0`). A row whose file is absent stays NULL and is
    counted in `file_missing`; a row whose file is byte-identical to cod-doc's
    own last export is counted in `skipped` — that file was produced by the
    renderer, so it has no authored shape to recover.

    `dry_run=True` classifies every row without writing anything.

    Caller owns the transaction.
    """
    stmt = select(DocumentModel).where(
        DocumentModel.project_id == project_id,
        or_(
            DocumentModel.frontmatter_raw.is_(None),
            DocumentModel.title_in_body.is_(None),
        ),
    )
    models = list(session.execute(stmt).scalars())

    items: list[FidelityBackfillItem] = []
    counts = {action: 0 for action in FidelityBackfillAction}

    for model in models:
        target = _safe_target(root_path, model.path)
        if not target.exists():
            action = FidelityBackfillAction.FILE_MISSING
        else:
            content = target.read_text(encoding="utf-8")
            if model.projection_hash is not None and _sha256(content) == model.projection_hash:
                action = FidelityBackfillAction.SKIPPED
            else:
                action = FidelityBackfillAction.FILLED
                if not dry_run:
                    raw, has_h1 = _leading_shape(content)
                    model.frontmatter_raw = raw or ""
                    model.title_in_body = has_h1
        counts[action] += 1
        items.append(FidelityBackfillItem(doc_key=model.doc_key, path=model.path, action=action))

    if not dry_run:
        session.flush()

    return FidelityBackfillReport(
        project_id=project_id,
        scanned=len(models),
        filled=counts[FidelityBackfillAction.FILLED],
        file_missing=counts[FidelityBackfillAction.FILE_MISSING],
        skipped=counts[FidelityBackfillAction.SKIPPED],
        items=items,
    )
