"""ADO-218: restore ``resolved`` / ``done`` document statuses the alias table folded into ``active``.

Until this revision ``DocumentStatus`` had no terminal status for documents that
describe finite work. A closed audit report says ``status: resolved`` in its
frontmatter, a closed plan or sprint says ``status: done`` — both are written by
``standards/frontmatter.md`` §2a — and ``_ALIEN_STATUS_ALIASES`` stored both as
``active``. The DB could not tell a closed audit from a live one; on the cod-doc
corpus itself 45 files carried one of the two values, and ``metadata_mismatch``
(ADO-092/ADO-216) reported every one of them.

The trap is the one 0033 describes for ``authoritative``: while a value is
unrepresentable, ``_raw_matches_db`` treats it as agreeing with the row. Once the
enum holds it, the comparison becomes ``'resolved' != 'active'`` and the next
export would rewrite the author's file to ``active``. So the authored value is
put back from ``frontmatter_json``, which the importer stored verbatim.

Narrower than 0033 on purpose: only rows still holding ``active`` — the exact
value the alias produced — are touched. A row whose status was later chosen in
the DB (``doc accept``, a web status change) is left alone even if an old
frontmatter copy still says ``resolved``.

Data-only: ``document.status`` is ``VARCHAR(32)`` without CHECK or ENUM.

``downgrade`` returns both values to ``active`` — what the alias table at the
previous revision produced for them — so a downgraded DB holds nothing the old
enum cannot parse.

Revision ID: 0040_document_status_resolved_done
Revises: 0039_totals_cancelled
Create Date: 2026-09-26
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "0040_document_status_resolved_done"
down_revision: str | None = "0039_totals_cancelled"
branch_labels = None
depends_on = None


# Hardcoded on purpose: a migration pins the shape of the world at its own
# revision and must not drift with `cod_doc.domain.entities`.
RECOVERABLE_STATUSES = frozenset({"resolved", "done"})

# What `_ALIEN_STATUS_ALIASES` stored both values as before this revision.
COERCED_TO = "active"


def _authored_status(frontmatter_json: object) -> str | None:
    """The ``status:`` the source file carried, if it is one this revision recovers.

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
    """Rewrite ``active`` rows to the authored ``resolved`` / ``done``.

    Parsed in Python rather than with ``json_extract`` / ``->>`` so the same
    code runs on SQLite and Postgres.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT row_id, frontmatter_json FROM document "
            "WHERE status = :coerced AND frontmatter_json IS NOT NULL"
        ),
        {"coerced": COERCED_TO},
    ).fetchall()

    for row_id, frontmatter_json in rows:
        authored = _authored_status(frontmatter_json)
        if authored is None:
            continue
        bind.execute(
            sa.text("UPDATE document SET status = :status WHERE row_id = :row_id"),
            {"status": authored, "row_id": row_id},
        )


def downgrade() -> None:
    """Fold both values back into ``active``, as the previous alias table did."""
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE document SET status = :coerced WHERE status IN :recoverable").bindparams(
            sa.bindparam("recoverable", expanding=True)
        ),
        {"coerced": COERCED_TO, "recoverable": sorted(RECOVERABLE_STATUSES)},
    )
