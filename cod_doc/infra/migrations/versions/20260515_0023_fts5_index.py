"""OBI-040: FTS5 virtual table for unified search over docs/tasks/stories/ADRs.

We pick SQLite FTS5 over external indexers because the project DB is
already SQLite — no extra runtime dependency, no second store to keep in
sync, no daemon.

Schema:
- ``db_search_idx`` — FTS5 virtual table. Columns: ``kind, ref, project_id
  UNINDEXED, title, body``. ``UNINDEXED`` keeps the column queryable but
  excludes it from tokenization (saves space; the column is just a filter
  predicate).
- Tokenizer ``unicode61 remove_diacritics 2`` — handles Russian + English
  out of the box without bringing in icu.
- Population happens via ``search_service.reindex_all`` — no triggers
  (the source-of-truth schema sprawls across 10+ tables and triggers per
  table would balloon).

Revision ID: 0023_fts5_index
Revises: 0022_repo_index
Create Date: 2026-05-15
"""

from __future__ import annotations

from alembic import op

revision = "0023_fts5_index"
down_revision = "0022_repo_index"
branch_labels = None
depends_on = None


def _require_sqlite() -> None:
    """SYM-002 (RFC 22 §3.1, находка B10): не делать вид, что FTS5 переносим.

    Без guard'а на Postgres миграция падала невнятной синтаксической ошибкой
    посреди `alembic upgrade head`.
    """
    dialect = op.get_bind().dialect.name
    if dialect != "sqlite":
        raise NotImplementedError(
            f"Миграция 0023_fts5_index использует SQLite FTS5 и не поддерживает "
            f"диалект {dialect!r}. Полнотекстовый поиск на Postgres требует "
            f"отдельной миграции (tsvector + GIN) — см. RFC 22 §3.1."
        )


def upgrade() -> None:
    _require_sqlite()
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS db_search_idx USING fts5(
            kind,
            ref,
            project_id UNINDEXED,
            title,
            body,
            tokenize = 'unicode61 remove_diacritics 2'
        );
    """)


def downgrade() -> None:
    _require_sqlite()
    op.execute("DROP TABLE IF EXISTS db_search_idx;")
