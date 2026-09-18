"""Переписать view ``document_body`` так, чтобы предикат по документу проталкивался.

Форма из 0025 собирала секции в производной таблице с ``GROUP BY document_id``
и джойнила её к ``document``. SQLite не умеет протолкнуть внешний
``WHERE document_id = ?`` внутрь такой группировки — он материализует агрегат
**по всей таблице ``section``** и только потом берёт одну строку. Значит любое
чтение одного документа стоит O(все секции проекта), а обход всех документов —
O(документы × секции).

Замер на живой БД репозитория (150 документов, 1200 секций): 150 чтений
``document_body`` — 278 мс, из них 341 мс кумулятивно в sqlite3 при прогоне
через SQLAlchemy. На этом стояла страница ``GET /p/{slug}``, которая гоняет
``detect_project_drift`` по всем документам: ~1 с на запрос.

Здесь агрегат переезжает в коррелированный скалярный подзапрос. Внешний
предикат становится ``SEARCH section USING INDEX ix_section_position
(document_id=?)`` — те же 150 чтений занимают 5 мс. Чтение всех строк разом
тоже не пострадало (8.4 мс → 5.1 мс).

Текст, который отдаёт view, не меняется: обе формы проверены на всех 150
документах живой БД — побайтово идентичны. Это важно, потому что от него
считается ``document.projection_hash``.

Внешний ``SELECT`` не содержит агрегатов и группировок, поэтому планировщик
его сплющивает и проносит предикат внутрь. Разделитель ADO-010 (пустая строка
между preamble и секциями) тестируется на уже вычисленном ``sec``, так что
агрегат по-прежнему записан один раз.

Revision ID: 0030_document_body_pushdown
Revises: 0029_drop_audit_log
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0030_document_body_pushdown"
down_revision: str | None = "0029_drop_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DOCUMENT_BODY_VIEW_SQLITE = """
CREATE VIEW document_body AS
SELECT
  document_id,
  preamble
    || CASE WHEN preamble <> '' AND sec <> '' THEN char(10) || char(10) ELSE '' END
    || sec AS body
FROM (
  SELECT
    d.row_id AS document_id,
    d.preamble AS preamble,
    COALESCE((
      SELECT group_concat(
        substr('######', 1, s.level) || ' ' || s.heading || char(10) || char(10) || s.body,
        char(10) || char(10)
      )
      FROM (
        SELECT level, heading, body
        FROM section
        WHERE document_id = d.row_id
        ORDER BY position
      ) s
    ), '') AS sec
  FROM document d
) AS t
"""

DOCUMENT_BODY_VIEW_POSTGRES = """
CREATE VIEW document_body AS
SELECT
  document_id,
  preamble
    || CASE WHEN preamble <> '' AND sec <> '' THEN E'\\n\\n' ELSE '' END
    || sec AS body
FROM (
  SELECT
    d.row_id AS document_id,
    d.preamble AS preamble,
    COALESCE((
      SELECT string_agg(
        repeat('#', s.level) || ' ' || s.heading || E'\\n\\n' || s.body,
        E'\\n\\n'
        ORDER BY s.position
      )
      FROM section s
      WHERE s.document_id = d.row_id
    ), '') AS sec
  FROM document d
) AS t
"""

# Форма 0025, восстанавливается downgrade()'ом дословно.
DOCUMENT_BODY_VIEW_SQLITE_V0025 = """
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

DOCUMENT_BODY_VIEW_POSTGRES_V0025 = """
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


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS document_body")
    op.execute(DOCUMENT_BODY_VIEW_SQLITE if _is_sqlite() else DOCUMENT_BODY_VIEW_POSTGRES)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS document_body")
    op.execute(
        DOCUMENT_BODY_VIEW_SQLITE_V0025 if _is_sqlite() else DOCUMENT_BODY_VIEW_POSTGRES_V0025
    )
