"""ADO-015: restore document types that were coerced before the enum knew them.

Until ADO-015 ``DocumentType`` held thirteen values, and ``import_markdown``
turned everything else into ``module-spec`` without a word. Whole classes of
real documents went in that way — this repository's own ``capability`` and
``audit-report`` docs, the pilots' ``design`` / ``journal`` / ``plan`` /
``analysis`` / ``research`` / ``audit``.

Adding those values to the enum is not enough on its own, and left alone would
make things *worse*. ``_raw_matches_db`` (ADO-010) protects a file whose
``type:`` the DB cannot represent by treating the unknown value as agreeing
with whatever the row holds. The moment ``capability`` becomes representable,
that protection lapses: the comparison becomes ``'capability' != 'module-spec'``
and the next export rewrites the author's file to the coerced type — exactly
the corruption ADO-010 exists to stop.

So this migration puts the authored value back, taking it from
``frontmatter_json``, which the importer stored verbatim all along. It touches
only rows where the frontmatter names one of the eight newly-representable
types and the stored type disagrees; a row whose type was chosen in the DB
(``doc create``) has no ``frontmatter_raw`` and is left alone.

Data-only: ``document.type`` is ``VARCHAR(32)`` with no CHECK and no database
ENUM, so nothing about the schema changes.

Revision ID: 0026_document_type_recoercion
Revises: 0025_projection_fidelity
Create Date: 2026-08-26
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0026_document_type_recoercion"
down_revision = "0025_projection_fidelity"
branch_labels = None
depends_on = None


# Hardcoded on purpose: a migration pins the shape of the world at its own
# revision and must not drift with `cod_doc.domain.entities`. These are the
# eight values ADO-015 added — the only ones that can have been coerced away.
RECOVERABLE_TYPES = frozenset(
    {
        "design",
        "audit",
        "audit-report",
        "journal",
        "plan",
        "analysis",
        "research",
        "capability",
    }
)


def _authored_type(frontmatter_json: object) -> str | None:
    """The ``type:`` the source file carried, if it is one we can now store.

    Accepts either the decoded mapping (Postgres JSON/JSONB) or the raw text
    SQLite hands back for the same column.
    """
    decoded: object = frontmatter_json
    if isinstance(decoded, (str, bytes)):
        try:
            decoded = json.loads(decoded)
        except (ValueError, TypeError):
            return None
    if not isinstance(decoded, dict):
        return None
    raw = decoded.get("type")
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    return candidate if candidate in RECOVERABLE_TYPES else None


def upgrade() -> None:
    """Rewrite ``document.type`` from the authored frontmatter where it differs.

    Parsed in Python rather than with ``json_extract`` / ``->>`` so the same
    code runs on SQLite and Postgres.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT row_id, type, frontmatter_json FROM document WHERE frontmatter_json IS NOT NULL"
        )
    ).fetchall()

    for row_id, stored_type, frontmatter_json in rows:
        authored = _authored_type(frontmatter_json)
        if authored is None or authored == stored_type:
            continue
        bind.execute(
            sa.text("UPDATE document SET type = :type WHERE row_id = :row_id"),
            {"type": authored, "row_id": row_id},
        )


def downgrade() -> None:
    """No-op — deliberately.

    Re-coercing these rows back to ``module-spec`` would destroy the authored
    value a second time, and it is not what a downgrade is for: an older
    cod-doc reading ``type: capability`` fails in ``DocumentRepository._to_domain``
    whether or not this migration ran, because the enum in *that* build has no
    such member. Roll the code back and the data stays honest; roll the data
    back and it never returns.
    """
