"""ADO-238: restore ``index`` / ``rfc`` document types the importer folded into ``module-spec``.

``DocumentType`` gains two genres the cod-doc corpus was already writing under
names the enum did not know: navigation documents (MASTER, ROADMAP) and
numbered change proposals (``proposals/NN-*``). An unknown ``type:`` falls back
to ``module-spec`` on import, so every such row holds ``module-spec`` today.

The trap is the one 0026 and 0040 describe: while a value is unrepresentable,
``_raw_matches_db`` treats it as agreeing with the row. Once the enum holds
``index``, a file that says ``type: index`` over a ``module-spec`` row is a
mismatch, and the next export would rewrite the author's file to
``module-spec``. So the authored value is put back from ``frontmatter_json``,
which the importer stored verbatim.

Narrow on purpose, like 0040: only rows still holding ``module-spec`` — the
exact value the fallback produced — are touched. Files that spelled the genre
differently (``documentation-master``, ``ux-proposal``, …) are not recovered
here: ADO-238 rewrites those files, and the re-import carries the new value.

Data-only: ``document.type`` is ``VARCHAR(32)`` without CHECK or ENUM.

``downgrade`` returns both values to ``module-spec`` — what the importer at the
previous revision produced for them — so a downgraded DB holds nothing the old
enum cannot parse.

Revision ID: 0044_document_type_index_rfc
Revises: 0043_plan_id_prefix
Create Date: 2026-10-03
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "0044_document_type_index_rfc"
down_revision: str | None = "0043_plan_id_prefix"
branch_labels = None
depends_on = None


# Hardcoded on purpose: a migration pins the shape of the world at its own
# revision and must not drift with `cod_doc.domain.entities`.
RECOVERABLE_TYPES = frozenset({"index", "rfc"})

# The fallback the importer stored an unknown `type:` as before this revision.
COERCED_TO = "module-spec"


def _authored_type(frontmatter_json: object) -> str | None:
    """The ``type:`` the source file carried, if it is one this revision recovers.

    Accepts either the decoded mapping (Postgres JSON/JSONB) or the raw text
    SQLite hands back for the same column.
    """
    decoded: object = frontmatter_json
    if isinstance(decoded, (str, bytes)):
        try:
            decoded = json.loads(decoded)
        except ValueError, TypeError:
            return None
    if not isinstance(decoded, dict):
        return None
    raw = decoded.get("type")
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    return candidate if candidate in RECOVERABLE_TYPES else None


def upgrade() -> None:
    """Rewrite ``module-spec`` rows to the authored ``index`` / ``rfc``.

    Parsed in Python rather than with ``json_extract`` / ``->>`` so the same
    code runs on SQLite and Postgres.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT row_id, frontmatter_json FROM document "
            "WHERE type = :coerced AND frontmatter_json IS NOT NULL"
        ),
        {"coerced": COERCED_TO},
    ).fetchall()

    for row_id, frontmatter_json in rows:
        authored = _authored_type(frontmatter_json)
        if authored is None:
            continue
        bind.execute(
            sa.text("UPDATE document SET type = :type WHERE row_id = :row_id"),
            {"type": authored, "row_id": row_id},
        )


def downgrade() -> None:
    """Fold both values back into ``module-spec``, as the previous importer did."""
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE document SET type = :coerced WHERE type IN :recoverable").bindparams(
            sa.bindparam("recoverable", expanding=True)
        ),
        {"coerced": COERCED_TO, "recoverable": sorted(RECOVERABLE_TYPES)},
    )
