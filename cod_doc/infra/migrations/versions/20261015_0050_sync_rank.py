"""0050: дробный ранг в 11 местах порядка; снятие четырёх UNIQUE на position.

RFC 29 §3.2 (Q-009), §4, §10.1 (CLO-003).

1. ``ALTER TABLE <t> ADD COLUMN rank TEXT`` в десяти упорядоченных таблицах и
   ``document.node_rank`` — порядок документа внутри раздела дерева. Только
   ``ADD COLUMN``: ``document`` и ``section`` не пересоздаются (CASCADE унёс бы
   секции и ссылки, CLAUDE.md).
2. Бэкфилл: внутри каждого родителя строки получают равномерные ранги в
   текущем порядке показа — ``position``, затем тай-брейк, которым этот
   порядок читал код до 0050 (``node_key`` у ``doc_node``, ``key`` у
   ``story_section``, ``scenario_id`` у ``scenario``, иначе ``row_id``). У
   ``document`` ранг получают только разложенные документы (``node_id IS NOT
   NULL``), порядок — ``node_position`` (NULL в конце), затем ``doc_key``.
   ``position`` не трогается: существующие выдачи не меняются.
3. ``UNIQUE (…, position)`` снимается у ``scenario_step``, ``story_acceptance``,
   ``adr_diagram``, ``open_question_option``: после слияния двух реплик две
   строки законно делят одну позицию до пересчёта. SQLite не умеет снимать
   ограничение — таблица пересобирается из своего SQL в ``sqlite_master`` с
   вырезанным ровно одним ограничением (паттерн 0047: ``foreign_keys=OFF``
   через ``autocommit_block`` с проверкой, представления снимаются и
   восстанавливаются, после — ``foreign_key_check`` по пересобранным). Вместо
   UNIQUE ставится обычный индекс ``ix_<t>_position`` на тех же колонках —
   поиск по позиции (``set_criterion_met``, опции вопроса) не деградирует до
   скана. Пересборка разрешена только таблице без дочерних внешних ключей:
   ``DROP TABLE`` при выключенных ключах детей не удалит, но оставит их с
   висящими ссылками на пересозданную таблицу — проверяется до изменений.

Миграция — застывший снимок: список мест, родители, тай-брейки и алгоритм
``initial`` — литералы, а не импорт ``cod_doc.infra.rank``/``sync_registry``.

Downgrade: проверка, что ни одна пара (родитель, position) не повторяется;
обратная пересборка с UNIQUE; ``DROP INDEX ix_<t>_position``; ``DROP COLUMN
rank`` / ``node_rank`` (SQLite ≥ 3.35 правит таблицу на месте, без CASCADE).

Revision ID: 0050_sync_rank
Revises: 0049_sync_identity
Create Date: 2026-10-15
"""

from __future__ import annotations

import re
from itertools import groupby
from typing import TYPE_CHECKING, NamedTuple

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Connection

revision: str = "0050_sync_rank"
down_revision: str | None = "0049_sync_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class _Place(NamedTuple):
    table: str
    column: str  # колонка ранга
    parent: tuple[str, ...]  # порядок внутри
    order: tuple[str, ...]  # текущий порядок показа внутри родителя
    where: str = ""  # какие строки упорядочены


#: Одиннадцать мест порядка RFC 29 §3.2.
PLACES: tuple[_Place, ...] = (
    _Place("section", "rank", ("document_id",), ("position", "row_id")),
    _Place("plan_section", "rank", ("plan_id",), ("position", "row_id")),
    _Place("doc_node", "rank", ("project_id",), ("position", "node_key", "row_id")),
    _Place("scenario", "rank", ("project_id", "group_key"), ("position", "scenario_id", "row_id")),
    _Place("scenario_step", "rank", ("scenario_row_id",), ("position", "row_id")),
    _Place("story_section", "rank", ("project_id",), ("position", '"key"', "row_id")),
    _Place("story_acceptance", "rank", ("story_id",), ("position", "row_id")),
    _Place("adr_diagram", "rank", ("adr_id",), ("position", "row_id")),
    _Place("adr_topic", "rank", ("project_id",), ("position", "row_id")),
    _Place("open_question_option", "rank", ("question_row_id",), ("position", "row_id")),
    _Place(
        "document",
        "node_rank",
        ("node_id",),
        ("node_position IS NULL", "node_position", "doc_key", "row_id"),
        "node_id IS NOT NULL",
    ),
)


class _Unique(NamedTuple):
    table: str
    constraint: str
    parent: str
    position_index: str  # обычный индекс на месте снятого UNIQUE


UNIQUES: tuple[_Unique, ...] = (
    _Unique(
        "scenario_step", "uq_scenario_step_position", "scenario_row_id", "ix_scenario_step_position"
    ),
    _Unique(
        "story_acceptance",
        "uq_story_acceptance_position",
        "story_id",
        "ix_story_acceptance_position",
    ),
    _Unique("adr_diagram", "uq_adr_diagram_position", "adr_id", "ix_adr_diagram_position"),
    _Unique(
        "open_question_option",
        "uq_open_question_option_position",
        "question_row_id",
        "ix_open_question_option_position",
    ),
)

# --- ранг: застывшая копия cod_doc.infra.rank.initial ------------------------

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_BASE = len(_DIGITS)


def _initial(n: int) -> list[str]:
    width = 2
    while _BASE**width < (n + 1) * _BASE:
        width += 1
    step = _BASE**width // (n + 1)
    ranks = []
    for i in range(n):
        value, digits = step * (i + 1), []
        for _ in range(width):
            value, rem = divmod(value, _BASE)
            digits.append(_DIGITS[rem])
        ranks.append("".join(reversed(digits)).rstrip("0"))
    return ranks


def _backfill(bind: Connection, place: _Place) -> None:
    where = f"WHERE {place.where} " if place.where else ""
    parent = ", ".join(place.parent)
    rows = bind.exec_driver_sql(
        f"SELECT row_id, {parent} FROM {place.table} {where}"
        f"ORDER BY {parent}, {', '.join(place.order)}"
    ).fetchall()
    width = len(place.parent)
    updates: list[dict[str, object]] = []
    for _key, group in groupby(rows, key=lambda row: tuple(row[1 : 1 + width])):
        ids = [row[0] for row in group]
        updates += [
            {"rank": rank, "row_id": row_id}
            for row_id, rank in zip(ids, _initial(len(ids)), strict=True)
        ]
    if updates:
        bind.execute(
            sa.text(f"UPDATE {place.table} SET {place.column} = :rank WHERE row_id = :row_id"),
            updates,
        )


# --- пересборка таблиц без UNIQUE (SQLite) -----------------------------------


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _children(bind: Connection, targets: set[str]) -> dict[str, list[str]]:
    """``таблица → [дочерние таблицы]``: кто ссылается внешним ключом на цели."""
    tables = [
        str(name)
        for (name,) in bind.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    found: dict[str, list[str]] = {}
    for table in tables:
        for row in bind.exec_driver_sql(
            f"PRAGMA foreign_key_list({_quote_ident(table)})"
        ).fetchall():
            if row[2] in targets:
                found.setdefault(str(row[2]), []).append(table)
    return found


def _unique_clause(target: _Unique) -> re.Pattern[str]:
    return re.compile(
        rf',\s*CONSTRAINT\s+"?{target.constraint}"?\s+UNIQUE\s*\(\s*"?{target.parent}"?\s*,'
        rf'\s*"?position"?\s*\)'
    )


def _swap(sql: str, target: _Unique, *, drop: bool) -> str:
    """Вырезать или вернуть ровно одно ограничение в CREATE TABLE; иначе — стоп."""
    if drop:
        new_sql, count = _unique_clause(target).subn("", sql)
    else:
        if _unique_clause(target).search(sql):
            count = 0
            new_sql = sql
        else:
            new_sql, count = re.subn(
                r"\s*\)\s*$",
                f",\n\tCONSTRAINT {target.constraint} UNIQUE ({target.parent}, position)\n)",
                sql,
                count=1,
            )
    if count != 1:
        raise RuntimeError(
            f"0050: в CREATE TABLE {target.table} ожидалось ровно одно место для "
            f"{target.constraint}, найдено {count}; схема не та, что ожидает миграция"
        )
    return new_sql


def _rename_create(sql: str, table: str, new_name: str) -> str:
    new_sql, count = re.subn(
        rf'^CREATE TABLE\s+"?{table}"?', f"CREATE TABLE {_quote_ident(new_name)}", sql, count=1
    )
    if count != 1:
        raise RuntimeError(f"0050: не разобран заголовок CREATE TABLE {table}")
    return new_sql


def _set_foreign_keys(bind: Connection, *, on: bool) -> None:
    # Внутри транзакции SQLite игнорирует эту прагму — поэтому autocommit_block.
    with op.get_context().autocommit_block():
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if on else 'OFF'}")


def _rebuild_sqlite(*, drop: bool) -> None:
    bind = op.get_bind()
    targets = {target.table for target in UNIQUES}
    children = _children(bind, targets)
    if children:
        raise RuntimeError(
            f"0050: у пересобираемых таблиц есть дочерние внешние ключи {children}; "
            "пересборка оставила бы их висящими — миграция остановлена до изменений"
        )
    _set_foreign_keys(bind, on=False)
    try:
        if bind.exec_driver_sql("PRAGMA foreign_keys").scalar() != 0:
            raise RuntimeError(
                "0050: внешние ключи не выключились — миграция остановлена до изменений"
            )
        views = bind.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type = 'view' ORDER BY rowid"
        ).fetchall()
        for name, _sql in reversed(views):
            op.execute(f"DROP VIEW {_quote_ident(name)}")

        for target in UNIQUES:
            table_sql = bind.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (target.table,),
            ).scalar_one()
            indexes = bind.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
                (target.table,),
            ).fetchall()
            tmp = f"{target.table}__0050"
            op.execute(_rename_create(_swap(table_sql, target, drop=drop), target.table, tmp))
            table, tmp_q = _quote_ident(target.table), _quote_ident(tmp)
            op.execute(f"INSERT INTO {tmp_q} SELECT * FROM {table}")
            for name, _sql in indexes:
                op.execute(f"DROP INDEX {_quote_ident(name)}")
            op.execute(f"DROP TABLE {table}")
            op.execute(f"ALTER TABLE {tmp_q} RENAME TO {table}")
            for _name, index_sql in indexes:
                op.execute(index_sql)

        for _name, view_sql in views:
            op.execute(view_sql)

        violations = [
            row
            for table in sorted(targets)
            for row in bind.exec_driver_sql(
                f"PRAGMA foreign_key_check({_quote_ident(table)})"
            ).fetchall()
        ]
        if violations:
            raise RuntimeError(f"0050: после пересборки нарушены внешние ключи: {violations[:5]}")
    finally:
        _set_foreign_keys(bind, on=True)


def upgrade() -> None:
    bind = op.get_bind()
    for place in PLACES:
        op.execute(f"ALTER TABLE {place.table} ADD COLUMN {place.column} TEXT")
        _backfill(bind, place)

    if bind.dialect.name == "sqlite":
        _rebuild_sqlite(drop=True)
    else:
        for target in UNIQUES:
            op.drop_constraint(target.constraint, target.table, type_="unique")
    for target in UNIQUES:
        op.create_index(target.position_index, target.table, [target.parent, "position"])


def _assert_positions_unique() -> None:
    bind = op.get_bind()
    for target in UNIQUES:
        duplicates = bind.execute(
            sa.text(
                f"SELECT {target.parent}, position FROM {target.table} "
                f"GROUP BY {target.parent}, position HAVING COUNT(*) > 1"
            )
        ).fetchall()
        if duplicates:
            raise RuntimeError(
                f"Cannot downgrade 0050: {len(duplicates)} duplicate "
                f"({target.parent}, position) pair(s) in {target.table}; "
                "renumber positions first."
            )


def downgrade() -> None:
    _assert_positions_unique()
    bind = op.get_bind()
    for target in UNIQUES:
        op.drop_index(target.position_index, table_name=target.table)
    if bind.dialect.name == "sqlite":
        _rebuild_sqlite(drop=False)
    else:
        for target in UNIQUES:
            op.create_unique_constraint(
                target.constraint, target.table, [target.parent, "position"]
            )
    for place in reversed(PLACES):
        op.execute(f"ALTER TABLE {place.table} DROP COLUMN {place.column}")
