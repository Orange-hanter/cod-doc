"""ADO-092: restore document statuses that were coerced before the enum knew them.

Until this revision ``DocumentStatus`` held four values, and every write path —
``import_markdown`` on create and on update alike — turned anything else into
``draft`` without a word. ``authoritative`` is what real corpora were authoring:
in the Gateway pilot alone, 36 documents went in that way, including all 24 ADRs,
the canonical architecture document and the business requirements.

Adding the value to the enum is not enough on its own, and left alone would make
things *worse* — the same trap ADO-015 hit and documented. ``_raw_matches_db``
(ADO-010) protects a file whose ``status:`` the DB cannot represent by treating
the unknown value as agreeing with whatever the row holds. The moment
``authoritative`` becomes representable that protection lapses: the comparison
becomes ``'authoritative' != 'draft'`` and the next export rewrites the author's
file to the coerced status — in 36 files at once, canonical ones included.

So this migration puts the authored value back, taking it from
``frontmatter_json``, which the importer stored verbatim all along. It touches
only rows whose frontmatter says ``authoritative`` and whose stored status
disagrees; a row whose status was chosen in the DB (``doc create``, ``doc
accept``) has no such frontmatter and is left alone.

Data-only: ``document.status`` is ``VARCHAR(32)`` with no CHECK and no database
ENUM, so nothing about the schema changes.

``downgrade`` puts those rows back to ``draft`` — the value the four-status enum
would have held — so the pair is symmetric and a downgrade does not leave the DB
holding a status the code cannot parse.

Revision ID: 0033_document_status_recoercion
Revises: 0032_scenarios
Create Date: 2026-09-17
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0033_document_status_recoercion"
down_revision = "0032_scenarios"
branch_labels = None
depends_on = None


# Hardcoded on purpose: a migration pins the shape of the world at its own
# revision and must not drift with `cod_doc.domain.entities`. This is the one
# value ADO-092 added — the only one that can have been coerced away by it.
RECOVERABLE_STATUSES = frozenset({"authoritative"})

# What the four-status enum coerced an unknown value into.
COERCED_TO = "draft"


def _authored_status(frontmatter_json: object) -> str | None:
    """The ``status:`` the source file carried, if it is one we can now store.

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
    raw = decoded.get("status")
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    return candidate if candidate in RECOVERABLE_STATUSES else None


def upgrade() -> None:
    """Rewrite ``document.status`` from the authored frontmatter where it differs.

    Parsed in Python rather than with ``json_extract`` / ``->>`` so the same
    code runs on SQLite and Postgres.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT row_id, status, frontmatter_json FROM document "
            "WHERE frontmatter_json IS NOT NULL"
        )
    ).fetchall()

    for row_id, stored_status, frontmatter_json in rows:
        authored = _authored_status(frontmatter_json)
        if authored is None or authored == stored_status:
            continue
        bind.execute(
            sa.text("UPDATE document SET status = :status WHERE row_id = :row_id"),
            {"status": authored, "row_id": row_id},
        )


def downgrade() -> None:
    """Coerce the recovered statuses back to what the old enum could hold."""
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE document SET status = :coerced WHERE status IN :recoverable").bindparams(
            sa.bindparam("recoverable", expanding=True)
        ),
        {"coerced": COERCED_TO, "recoverable": sorted(RECOVERABLE_STATUSES)},
    )
