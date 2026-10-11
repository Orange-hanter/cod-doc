"""STO-018, STO-024: plan.scope, user_story.story_id, module.module_id — UNIQUE в проекте.

До этой ревизии три натуральных ключа были уникальны на всю БД:
``UNIQUE (scope)``, ``UNIQUE (story_id)``, ``UNIQUE (module_id)``. Миграция 0027
починила только ``task``; два проекта с планом ``cod-doc-bootstrap``, историей
``US-001`` или модулем ``M1-auth`` не уживались в одной hub-БД, а клон проекта
по RFC 29 падал бы на вставке. Теперь ключ уникален в паре с ``project_id``.

SQLite не умеет снимать ограничение, поэтому таблица пересобирается. У всех
трёх есть CASCADE-дети (``plan_section`` и ``task``; ``story_acceptance``,
``story_link``, ``story_tag``; ``module_code``, ``module_dependency``), и
``DROP TABLE`` при включённых внешних ключах удалил бы их каскадом. Отличие от
0027 — как выключаются ключи: ``PRAGMA foreign_keys=OFF`` внутри открытой
транзакции SQLite молча игнорирует, а 0027 срабатывала только потому, что
драйвер открывает транзакцию лениво. Здесь прагма идёт через
``autocommit_block``, её значение проверяется перед первым ``DROP``, а после
пересборки ``PRAGMA foreign_key_check`` должен вернуть пусто.

Таблица пересоздаётся из её собственного SQL в ``sqlite_master`` с заменой
ровно одного ограничения — так в новую таблицу переходят все колонки, в том
числе добавленные позже через ``ADD COLUMN`` (``plan.id_prefix``), без ручного
перечисления. Представления (``plan_totals`` ссылается на ``plan``) снимаются и
восстанавливаются дословно.

Revision ID: 0047_project_scoped_uniques
Revises: 0046_adr_topics
Create Date: 2026-10-11
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

revision = "0047_project_scoped_uniques"
down_revision = "0046_adr_topics"
branch_labels = None
depends_on = None


class _Target(NamedTuple):
    table: str
    column: str
    constraint: str  # имя нового проектного ограничения
    pg_global: str  # автоимя старого глобального ограничения на PostgreSQL


_TARGETS: tuple[_Target, ...] = (
    _Target("plan", "scope", "uq_plan_project_scope", "plan_scope_key"),
    _Target("user_story", "story_id", "uq_user_story_project_story_id", "user_story_story_id_key"),
    _Target("module", "module_id", "uq_module_project_module_id", "module_module_id_key"),
)


def _global_clause(column: str) -> re.Pattern[str]:
    return re.compile(rf'UNIQUE\s*\(\s*"?{column}"?\s*\)')


def _scoped_clause(target: _Target) -> re.Pattern[str]:
    return re.compile(
        rf'CONSTRAINT\s+"?{target.constraint}"?\s+UNIQUE\s*\(\s*"?project_id"?\s*,'
        rf'\s*"?{target.column}"?\s*\)'
    )


def _swap(sql: str, target: _Target, *, to_scoped: bool) -> str:
    """Заменить ровно одно ограничение в CREATE TABLE; иначе — остановиться."""
    if to_scoped:
        pattern = _global_clause(target.column)
        repl = f"CONSTRAINT {target.constraint} UNIQUE (project_id, {target.column})"
    else:
        pattern = _scoped_clause(target)
        repl = f"UNIQUE ({target.column})"
    new_sql, count = pattern.subn(repl, sql)
    if count != 1:
        raise RuntimeError(
            f"0047: в CREATE TABLE {target.table} ожидалось ровно одно ограничение "
            f"для замены, найдено {count}; схема не та, что ожидает миграция"
        )
    return new_sql


def _rename_create(sql: str, table: str, new_name: str) -> str:
    new_sql, count = re.subn(
        rf'^CREATE TABLE\s+"?{table}"?', f'CREATE TABLE "{new_name}"', sql, count=1
    )
    if count != 1:
        raise RuntimeError(f"0047: не разобран заголовок CREATE TABLE {table}")
    return new_sql


def _set_foreign_keys(bind: Connection, *, on: bool) -> None:
    # Внутри транзакции SQLite игнорирует эту прагму — поэтому autocommit_block.
    with op.get_context().autocommit_block():
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if on else 'OFF'}")


def _rebuild_sqlite(*, to_scoped: bool) -> None:
    bind = op.get_bind()
    _set_foreign_keys(bind, on=False)
    try:
        if bind.exec_driver_sql("PRAGMA foreign_keys").scalar() != 0:
            raise RuntimeError(
                "0047: внешние ключи не выключились; DROP TABLE удалил бы дочерние "
                "строки каскадом — миграция остановлена до изменений"
            )
        # Порядок создания (rowid в sqlite_master), а не имени: представление,
        # зависящее от другого, когда-то создавалось после него — значит, в этом
        # порядке их можно восстановить. Снимаем в обратном: сначала зависимые.
        # Порядок — гигиена, не условие корректности: SQLite не разрешает ссылки
        # при CREATE VIEW, только при чтении (проверено на 3.53, тест
        # test_migration_0047_view_order), так что съехавший rowid не ломает пересоздание.
        views = bind.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type = 'view' ORDER BY rowid"
        ).fetchall()
        for name, _sql in reversed(views):
            op.execute(f'DROP VIEW "{name}"')

        for target in _TARGETS:
            table_sql = bind.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (target.table,),
            ).scalar_one()
            indexes = bind.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
                (target.table,),
            ).fetchall()
            tmp = f"{target.table}__0047"
            op.execute(
                _rename_create(_swap(table_sql, target, to_scoped=to_scoped), target.table, tmp)
            )
            op.execute(f'INSERT INTO "{tmp}" SELECT * FROM "{target.table}"')
            for name, _sql in indexes:
                op.execute(f'DROP INDEX "{name}"')
            op.execute(f'DROP TABLE "{target.table}"')
            op.execute(f'ALTER TABLE "{tmp}" RENAME TO "{target.table}"')
            for _name, index_sql in indexes:
                op.execute(index_sql)

        for _name, view_sql in views:
            op.execute(view_sql)

        violations = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"0047: после пересборки нарушены внешние ключи: {violations[:5]}")
    finally:
        _set_foreign_keys(bind, on=True)


def _assert_no_cross_project_duplicates() -> None:
    bind = op.get_bind()
    for target in _TARGETS:
        duplicates = bind.execute(
            sa.text(
                f"SELECT {target.column} FROM {target.table} "
                f"GROUP BY {target.column} HAVING COUNT(DISTINCT project_id) > 1"
            )
        ).fetchall()
        if duplicates:
            raise RuntimeError(
                f"Cannot downgrade 0047: {len(duplicates)} {target.table}.{target.column} "
                "value(s) are shared across projects. Remove the duplicates first."
            )


def upgrade() -> None:
    """UNIQUE(col) → UNIQUE(project_id, col) для трёх натуральных ключей."""
    if op.get_bind().dialect.name == "sqlite":
        _rebuild_sqlite(to_scoped=True)
        return
    for target in _TARGETS:
        op.drop_constraint(_pg_global_unique_name(target), target.table, type_="unique")
        op.create_unique_constraint(target.constraint, target.table, ["project_id", target.column])


def _pg_global_unique_name(target: _Target) -> str:
    """Имя действующего глобального UNIQUE(col) на PostgreSQL.

    Ищется по составу колонок, а не берётся из константы: автоимя
    ``<table>_<col>_key`` верно только для ограничения, созданного без явного
    имени. Константа ``pg_global`` — запасной вариант и имя для downgrade.
    """
    for unique in sa.inspect(op.get_bind()).get_unique_constraints(target.table):
        if list(unique["column_names"]) == [target.column] and unique["name"]:
            return str(unique["name"])
    raise RuntimeError(
        f"0047: на {target.table} не найден UNIQUE({target.column}); схема не та, "
        "что ожидает миграция"
    )


def downgrade() -> None:
    """Вернуть глобальные UNIQUE — только пока значения не пересекаются между проектами."""
    _assert_no_cross_project_duplicates()
    if op.get_bind().dialect.name == "sqlite":
        _rebuild_sqlite(to_scoped=False)
        return
    for target in _TARGETS:
        op.drop_constraint(target.constraint, target.table, type_="unique")
        op.create_unique_constraint(target.pg_global, target.table, [target.column])
