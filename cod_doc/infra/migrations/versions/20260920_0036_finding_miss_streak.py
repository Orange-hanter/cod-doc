"""Серия промахов у находки: ``finding.miss_streak``.

До этой колонки ``reconcile_partition`` закрывала находку с первого же
прогона, на котором её не увидела. Для детерминированных правил это верно —
они не промахиваются. Для вердикта модели это неверно: он субъективен и
мигает. Замер на живом корпусе (``qwen3.7-flash``, три прогона подряд, один и
тот же промпт) дал наборы {architecture, data-model, scenarios},
{architecture, data-model}, {architecture, data-model} — ядро стабильно, хвост
плавает. Каждый такой хвост производил пару событий
``finding.resolved`` / ``finding.reopened`` на прогон и дёргал очередь
куратора.

Счётчик, а не пятый статус. Словарь статусов ``finding`` — контракт RFC 22
§3.2 (``open|resolved|dismissed|promoted``), он торчит наружу в MCP. Приём
``open → pending_verify → resolved`` из ``structure_drift`` живёт в другой
таблице с другим словарём, спроектированным под это в RFC 24, и главное — он
бы находку **прятал**: ``curator_next`` и экран навигатора фильтруют ровно
``status="open"``. Цель обратная: не закрывать преждевременно, но и не терять
из виду. С колонкой находка с одним промахом остаётся ``open`` и видимой.

Все существующие строки получают ноль и ведут себя ровно как раньше:
гистерезис включается параметром ``close_after_misses`` у конкретной
партиции, а по умолчанию он равен единице.

Revision ID: 0036_finding_miss_streak
Revises: 0035_doc_nodes
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0036_finding_miss_streak"
down_revision: str | None = "0035_doc_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Колонка добавляется обычным ``ALTER TABLE ADD COLUMN``, **не**
# ``batch_alter_table``.
#
# Оговорка про пределы этого довода, чтобы следующий не пересказывал её
# сильнее, чем она есть. На ``finding.row_id`` висит
# ``finding_source_run.finding_id`` с ``ON DELETE CASCADE``, а batch на SQLite
# пересоздаёт таблицу целиком: копия во временную, DROP старой, RENAME.
# Напрашивается вывод, что DROP унесёт журнал по каскаду, — **проверено, не
# уносит**: alembic снимает ``PRAGMA foreign_keys`` на время перестройки, и
# строка журнала переживает batch-вариант этой же миграции.
#
# Прямой DDL здесь не потому, что batch «уронит данные», а потому что
# перестраивать таблицу с детьми на каскаде ради одной колонки — риск без
# выгоды: ADD COLUMN SQLite умеет нативно, ничего не копируется и не дропается.
# Отдельно помнить, что batch на ``document`` в 0035 данные действительно
# терял: там в игре был ещё и view ``document_body``.


def upgrade() -> None:
    # ``server_default`` обязателен: без него ``NOT NULL`` не пройдёт по уже
    # существующим строкам.
    op.add_column(
        "finding",
        sa.Column("miss_streak", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    # Симметрично upgrade'у: обычный DROP COLUMN, без перестройки таблицы.
    # SQLite ≥ 3.35 умеет его нативно (прецедент — 0035).
    op.execute("ALTER TABLE finding DROP COLUMN miss_streak")
