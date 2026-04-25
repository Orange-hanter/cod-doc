"""Section A audit follow-ups: document_body view + server defaults + integrity checks.

Closes findings from docs/system/audit/2026-04-25-section-a-data-core.md:
- HI-1: document_body view (§4.3a) — dialect-aware DDL.
- HI-2: server_default = '{}' on NOT NULL JSON columns
        (project.config_json, document.frontmatter_json, audit_log.payload_json).
- ME-4: CHECK no-self-loop on dependency / module_dependency.
- LO-7: UNIQUE(story_id, position) on story_acceptance.

Revision ID: 0006_views_and_defaults
Revises: 0005_links_tags
Create Date: 2026-04-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_views_and_defaults"
down_revision: str | None = "0005_links_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# `ready_tasks` view (§4.3) — original definition copied verbatim from 0002.
# We drop+recreate it because batch_alter on `dependency` (SQLite temp-rename)
# would otherwise invalidate the view.
READY_TASKS_VIEW = """
CREATE VIEW ready_tasks AS
SELECT t.*
FROM task t
WHERE t.status = 'pending'
  AND NOT EXISTS (
    SELECT 1
    FROM dependency d
    JOIN task dep ON dep.row_id = d.to_task_id
    WHERE d.from_task_id = t.row_id
      AND d.kind = 'blocks'
      AND dep.status <> 'done'
  )
"""

# `document_body` view (§4.3a) — dialect-aware. Both forms produce identical
# output: preamble + "\n\n".join(f"{'#' * level} {heading}\n\n{body}") in
# section.position order.
DOCUMENT_BODY_VIEW_SQLITE = """
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

DOCUMENT_BODY_VIEW_POSTGRES = """
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


def upgrade() -> None:
    # HI-2: server_default '{}' on NOT NULL JSON columns.
    json_default = sa.text("'{}'")
    with op.batch_alter_table("project") as batch:
        batch.alter_column("config_json", server_default=json_default)
    with op.batch_alter_table("document") as batch:
        batch.alter_column("frontmatter_json", server_default=json_default)
    with op.batch_alter_table("audit_log") as batch:
        batch.alter_column("payload_json", server_default=json_default)

    # ME-4: CHECK no-self-loop on dependency edges. SQLite recreates the
    # `dependency` table via temp-rename, which invalidates `ready_tasks`
    # (depends on dependency) — drop and recreate around the alter.
    op.execute("DROP VIEW IF EXISTS ready_tasks")
    with op.batch_alter_table("dependency") as batch:
        batch.create_check_constraint(
            "ck_dependency_no_self_loop", "from_task_id <> to_task_id"
        )
    op.execute(READY_TASKS_VIEW)

    with op.batch_alter_table("module_dependency") as batch:
        batch.create_check_constraint(
            "ck_module_dependency_no_self_loop", "from_module <> to_module"
        )

    # LO-7: UNIQUE acceptance position within a story.
    with op.batch_alter_table("story_acceptance") as batch:
        batch.create_unique_constraint(
            "uq_story_acceptance_position", ["story_id", "position"]
        )

    # HI-1: document_body view. Created LAST: SQLite's batch_alter on `document`
    # recreates the table via temp-rename, which would invalidate any view
    # referencing it during the rename step.
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    op.execute(DOCUMENT_BODY_VIEW_SQLITE if is_sqlite else DOCUMENT_BODY_VIEW_POSTGRES)


def downgrade() -> None:
    # Drop view first — see upgrade() comment on SQLite batch-rename + view deps.
    op.execute("DROP VIEW IF EXISTS document_body")

    with op.batch_alter_table("story_acceptance") as batch:
        batch.drop_constraint("uq_story_acceptance_position", type_="unique")

    with op.batch_alter_table("module_dependency") as batch:
        batch.drop_constraint("ck_module_dependency_no_self_loop", type_="check")

    # Same SQLite temp-rename concern as in upgrade(): drop ready_tasks before
    # touching `dependency`, restore the original definition after.
    op.execute("DROP VIEW IF EXISTS ready_tasks")
    with op.batch_alter_table("dependency") as batch:
        batch.drop_constraint("ck_dependency_no_self_loop", type_="check")
    op.execute(READY_TASKS_VIEW)

    with op.batch_alter_table("audit_log") as batch:
        batch.alter_column("payload_json", server_default=None)
    with op.batch_alter_table("document") as batch:
        batch.alter_column("frontmatter_json", server_default=None)
    with op.batch_alter_table("project") as batch:
        batch.alter_column("config_json", server_default=None)
