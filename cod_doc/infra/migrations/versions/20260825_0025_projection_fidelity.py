"""ADO-010 (F7): round-trip fidelity — separator in ``document_body`` + ``frontmatter_raw``.

Two defects made ``doc export`` corrupt documents (audit 2026-07-29, F7):

1. The ``document_body`` view concatenated ``preamble`` directly with the first
   section heading. ``preamble`` is stored stripped of trailing newlines, so the
   projection produced ``…заранее.## 1. Зачем`` — a blockquote and an H2 glued
   into one line. This migration recreates the view with a ``\\n\\n`` separator
   emitted only when both sides are non-empty.
2. Frontmatter was re-serialised from JSON on every export, which reordered keys,
   quoted dates and rewrote flow-style lists as block lists (40 of 71 repo docs
   use flow style). ``document.frontmatter_raw`` keeps the verbatim YAML block
   from the imported file so the renderer can re-emit it byte-for-byte while the
   DB-authoritative fields still agree with it. ``""`` records "the file had no
   frontmatter", which NULL (never imported) must not be confused with.

``document.title_in_body`` records whether the source carried an ``# H1``. The
importer consumes that line into ``document.title``, so the renderer has to put
it back — but only for files that had one; inventing a heading in a file that
never had one is the same class of damage.

Revision ID: 0025_projection_fidelity
Revises: 0024_link_adr_ref
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_projection_fidelity"
down_revision = "0024_link_adr_ref"
branch_labels = None
depends_on = None


# The section block is computed once in a derived table and joined, so the
# separator can test it without repeating the aggregate. Both dialects produce
# `preamble + "\n\n" + sections` when both parts are non-empty, and just the
# non-empty part otherwise.
DOCUMENT_BODY_VIEW_SQLITE = """
CREATE VIEW document_body AS
SELECT
  d.row_id AS document_id,
  d.preamble
    || CASE
         WHEN d.preamble <> '' AND COALESCE(s.body, '') <> ''
         THEN char(10) || char(10)
         ELSE ''
       END
    || COALESCE(s.body, '') AS body
FROM document d
LEFT JOIN (
  SELECT
    document_id,
    group_concat(
      substr('######', 1, level) || ' ' || heading || char(10) || char(10) || body,
      char(10) || char(10)
    ) AS body
  FROM (
    SELECT document_id, level, heading, body
    FROM section
    ORDER BY document_id, position
  )
  GROUP BY document_id
) s ON s.document_id = d.row_id
"""

DOCUMENT_BODY_VIEW_POSTGRES = """
CREATE VIEW document_body AS
SELECT
  d.row_id AS document_id,
  d.preamble
    || CASE
         WHEN d.preamble <> '' AND COALESCE(s.body, '') <> ''
         THEN E'\\n\\n'
         ELSE ''
       END
    || COALESCE(s.body, '') AS body
FROM document d
LEFT JOIN (
  SELECT
    document_id,
    string_agg(
      repeat('#', level) || ' ' || heading || E'\\n\\n' || body,
      E'\\n\\n'
      ORDER BY position
    ) AS body
  FROM section
  GROUP BY document_id
) s ON s.document_id = d.row_id
"""

# Pre-ADO-010 form, restored verbatim by downgrade().
DOCUMENT_BODY_VIEW_SQLITE_V0006 = """
CREATE VIEW document_body AS
SELECT
  d.row_id AS document_id,
  d.preamble || COALESCE((
    SELECT group_concat(
      substr('######', 1, s.level) || ' ' || s.heading
        || char(10) || char(10) || s.body,
      char(10) || char(10)
    )
    FROM (
      SELECT level, heading, body
      FROM section
      WHERE document_id = d.row_id
      ORDER BY position
    ) s
  ), '') AS body
FROM document d
"""

DOCUMENT_BODY_VIEW_POSTGRES_V0006 = """
CREATE VIEW document_body AS
SELECT
  d.row_id AS document_id,
  d.preamble || COALESCE(string_agg(
    repeat('#', s.level) || ' ' || s.heading || E'\\n\\n' || s.body,
    E'\\n\\n'
    ORDER BY s.position
  ), '') AS body
FROM document d
LEFT JOIN section s ON s.document_id = d.row_id
GROUP BY d.row_id, d.preamble
"""


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    # Drop the view before touching `document`: SQLite's batch_alter recreates
    # the table via temp-rename, which would invalidate a view referencing it.
    op.execute("DROP VIEW IF EXISTS document_body")
    with op.batch_alter_table("document") as batch:
        batch.add_column(sa.Column("frontmatter_raw", sa.Text(), nullable=True))
        batch.add_column(sa.Column("title_in_body", sa.Boolean(), nullable=True))
    op.execute(DOCUMENT_BODY_VIEW_SQLITE if _is_sqlite() else DOCUMENT_BODY_VIEW_POSTGRES)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS document_body")
    with op.batch_alter_table("document") as batch:
        batch.drop_column("title_in_body")
        batch.drop_column("frontmatter_raw")
    op.execute(
        DOCUMENT_BODY_VIEW_SQLITE_V0006 if _is_sqlite() else DOCUMENT_BODY_VIEW_POSTGRES_V0006
    )
