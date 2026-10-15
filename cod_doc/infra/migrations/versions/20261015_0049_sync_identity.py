"""0049: глобальная идентичность — uid во authored-таблицах и служебные таблицы sync.

RFC 29 §3.3, §4, §10.1 (CLO-002).

1. ``ALTER TABLE <t> ADD COLUMN uid TEXT`` во всех authored-таблицах, кроме
   тег-таблиц с составным PK (``document_tag``/``task_tag``/``story_tag``: их
   uid вычисляется при захвате, §3.3). Бэкфилл: рёбра — uuid5 от JSON
   ``[таблица, *ключ]``, где FK ключа заменены на uid концов; остальные строки —
   ``uuid.uuid7()``. Затем ``CREATE UNIQUE INDEX uq_<t>_uid``. Колонка
   nullable: ``ADD COLUMN NOT NULL`` без ``DEFAULT`` на SQLite невозможен, а
   пересоздавать ``document`` нельзя — ``batch_alter_table`` унёс бы по CASCADE
   все секции и ссылки (CLAUDE.md). NOT NULL обеспечивают захват и тест.
2. Таблицы ``replica``, ``sync_op`` (колонка ``replica_uid``, §10.1, а не
   ``replica_id`` из §3.4), ``changeset``, ``sync_ref``, ``sync_lease``,
   ``entity_alias``.

Миграция — застывший снимок: список таблиц, ключи рёбер и пространство имён
uuid5 — литералы, а не импорт ``sync_registry``, который будет меняться.
Совпадение со снимком реестра стережёт
``tests/infra/test_migration_0049_preserves_data.py``.

Дубль ребра (одинаковый ключ в двух строках) получил бы один uid и уронил бы
уникальный индекс. UNIQUE на ключе есть у всех рёбер, кроме ``story_link``;
дубль там миграция не удаляет молча, а отказывает с именем таблицы.

Downgrade: снять индексы, ``DROP COLUMN uid`` (SQLite ≥ 3.35 правит таблицу на
месте, без пересоздания и без CASCADE), удалить шесть таблиц.

Revision ID: 0049_sync_identity
Revises: 0048_totals_project_scoped
Create Date: 2026-10-15
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0049_sync_identity"
down_revision: str | None = "0048_totals_project_scoped"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Authored-таблицы с ``row_id``-PK на момент 0049.
UID_TABLES: tuple[str, ...] = (
    "adr",
    "adr_diagram",
    "adr_relation",
    "adr_supersedes",
    "adr_task",
    "adr_topic",
    "affected_file",
    "approval",
    "approval_doc_revision_link",
    "approval_task_link",
    "commit_link",
    "dependency",
    "doc_comment",
    "doc_node",
    "document",
    "external_ref",
    "finding",
    "module",
    "module_code",
    "module_dependency",
    "open_question",
    "open_question_link",
    "open_question_option",
    "plan",
    "plan_section",
    "project",
    "routine",
    "scenario",
    "scenario_link",
    "scenario_step",
    "section",
    "story_acceptance",
    "story_link",
    "story_section",
    "structure_waiver",
    "tag",
    "task",
    "task_document",
    "user_story",
)

#: Рёбра: колонки ключа (порядок = ``deterministic_uid`` реестра) и FK среди
#: них → таблица конца, чей uid подставляется вместо ``row_id``.
EDGE_KEYS: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    "adr_relation": (("from_id", "to_id", "kind"), {"from_id": "adr", "to_id": "adr"}),
    "adr_supersedes": (
        ("superseding_id", "superseded_id"),
        {"superseding_id": "adr", "superseded_id": "adr"},
    ),
    "adr_task": (("adr_row_id", "task_id", "relation"), {"adr_row_id": "adr"}),
    "affected_file": (("task_id", "path"), {"task_id": "task"}),
    "commit_link": (("project_id", "task_id", "sha"), {"project_id": "project"}),
    "dependency": (
        ("from_task_id", "to_task_id", "kind"),
        {"from_task_id": "task", "to_task_id": "task"},
    ),
    "module_dependency": (
        ("from_module", "to_module"),
        {"from_module": "module", "to_module": "module"},
    ),
    "open_question_link": (
        ("question_row_id", "to_kind", "to_ref", "relation"),
        {"question_row_id": "open_question"},
    ),
    "scenario_link": (
        ("scenario_row_id", "to_kind", "to_ref", "relation"),
        {"scenario_row_id": "scenario"},
    ),
    "story_link": (("story_id", "to_kind", "to_ref", "relation"), {"story_id": "user_story"}),
}

#: Пространство имён uuid5 рёбер — копия ``sync_registry.EDGE_UID_NAMESPACE``.
EDGE_UID_NAMESPACE = "03a4b384-e781-5d7b-90ee-d862b599d935"


def _q(name: str) -> str:
    """Имя в SQL строкой — только из литералов этого модуля."""
    if not name.replace("_", "").isalpha() or not name.islower():
        msg = f"0049: недопустимое имя {name!r}"
        raise ValueError(msg)
    return f'"{name}"'


def edge_uid(table: str, values: Sequence[object]) -> str:
    """uuid5 от JSON ``[таблица, *значения ключа]``; FK уже переведены в uid."""
    canonical = json.dumps([table, *values], ensure_ascii=False, separators=(",", ":"))
    return str(uuid.uuid5(uuid.UUID(EDGE_UID_NAMESPACE), canonical))


def _refuse_duplicate_edges(conn: sa.Connection, table: str, columns: tuple[str, ...]) -> None:
    cols = ", ".join(_q(c) for c in columns)
    duplicates = conn.execute(
        sa.text(
            f"SELECT count(*) FROM (SELECT 1 FROM {_q(table)} GROUP BY {cols} HAVING count(*) > 1)"
        )
    ).scalar_one()
    if duplicates:
        msg = (
            f"0049: {table}: {duplicates} ключей ({', '.join(columns)}) повторяются —"
            " ребро с одним ключом получило бы один uid. Удалите дубли и повторите."
        )
        raise RuntimeError(msg)


def _refuse_dangling_endpoints(conn: sa.Connection, table: str, fks: dict[str, str]) -> None:
    for column, target in fks.items():
        dangling = conn.execute(
            sa.text(
                f"SELECT count(*) FROM {_q(table)} e LEFT JOIN {_q(target)} t"
                f" ON t.row_id = e.{_q(column)} WHERE t.row_id IS NULL"
            )
        ).scalar_one()
        if dangling:
            msg = f"0049: {table}: {dangling} строк с {column} без строки в {target}"
            raise RuntimeError(msg)


def _preflight(conn: sa.Connection) -> None:
    """Отказ — до первого DDL.

    pysqlite не открывает транзакцию перед DDL, и ``ADD COLUMN`` до отказа
    остался бы в БД при ревизии 0048.
    """
    for table, (columns, fks) in EDGE_KEYS.items():
        _refuse_duplicate_edges(conn, table, columns)
        _refuse_dangling_endpoints(conn, table, fks)


def _uids(conn: sa.Connection, table: str) -> dict[int, str]:
    return dict(conn.execute(sa.text(f"SELECT row_id, uid FROM {_q(table)}")).tuples().all())


def _write(conn: sa.Connection, table: str, rows: list[dict[str, object]]) -> None:
    if rows:
        conn.execute(sa.text(f"UPDATE {_q(table)} SET uid = :uid WHERE row_id = :row_id"), rows)


def _backfill_random(conn: sa.Connection, table: str) -> None:
    row_ids = conn.execute(sa.text(f"SELECT row_id FROM {_q(table)} ORDER BY row_id")).scalars()
    _write(conn, table, [{"row_id": r, "uid": str(uuid.uuid7())} for r in row_ids])


def _backfill_edges(conn: sa.Connection, table: str) -> None:
    columns, fks = EDGE_KEYS[table]
    endpoints = {column: _uids(conn, target) for column, target in fks.items()}
    cols = ", ".join(_q(c) for c in columns)
    rows: list[dict[str, object]] = []
    for row_id, *values in conn.execute(sa.text(f"SELECT row_id, {cols} FROM {_q(table)}")):
        key: list[object] = []
        for column, value in zip(columns, values, strict=True):
            if column not in endpoints:
                key.append(value)
                continue
            end_uid = endpoints[column].get(value)
            if end_uid is None:
                msg = f"0049: {table}.row_id={row_id}: {column}={value} без строки конца"
                raise RuntimeError(msg)
            key.append(end_uid)
        rows.append({"row_id": row_id, "uid": edge_uid(table, key)})
    _write(conn, table, rows)


def _create_sync_tables() -> None:
    project_fk = sa.ForeignKey("project.row_id", ondelete="CASCADE")
    op.create_table(
        "replica",
        sa.Column("uid", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Integer(), project_fk, nullable=False),
        sa.Column("is_self", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_replica_self",
        "replica",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("is_self = 1"),
        postgresql_where=sa.text("is_self"),
    )
    op.create_table(
        "sync_op",
        sa.Column("op_id", sa.Text(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("replica_uid", sa.Text(), nullable=False),
        sa.Column("hlc", sa.Text(), nullable=False),
        sa.Column("entity_kind", sa.Text(), nullable=False),
        sa.Column("entity_uid", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("fields", sa.Text(), nullable=False),
        sa.Column("base", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("changeset", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('upsert','delete')", name="ck_sync_op_kind"),
    )
    op.create_index(
        "ix_sync_op_pending",
        "sync_op",
        ["project_id"],
        sqlite_where=sa.text("changeset IS NULL"),
        postgresql_where=sa.text("changeset IS NULL"),
    )
    op.create_index("ix_sync_op_entity", "sync_op", ["entity_kind", "entity_uid"])
    op.create_table(
        "changeset",
        sa.Column("sha", sa.Text(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parents", sa.Text(), nullable=False),
        sa.Column("replica_uid", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("epoch", sa.Text(), nullable=False),
        sa.Column("hlc_max", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "sync_ref",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("remote", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("sha", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "sync_lease",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("prefix", sa.Text(), primary_key=True),
        sa.Column("next_number", sa.Integer(), nullable=False),
        sa.Column("end_number", sa.Integer(), nullable=False),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "entity_alias",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("entity_kind", sa.Text(), primary_key=True),
        sa.Column("alias", sa.Text(), primary_key=True),
        sa.Column("uid", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reason IN ('pinned','merged')", name="ck_entity_alias_reason"),
    )


_SYNC_TABLES = ("entity_alias", "sync_lease", "sync_ref", "changeset", "sync_op", "replica")


def upgrade() -> None:
    conn = op.get_bind()
    _preflight(conn)
    for table in UID_TABLES:
        op.execute(f"ALTER TABLE {_q(table)} ADD COLUMN uid TEXT")
    for table in UID_TABLES:
        if table not in EDGE_KEYS:
            _backfill_random(conn, table)
    for table in UID_TABLES:
        if table in EDGE_KEYS:
            _backfill_edges(conn, table)
    for table in UID_TABLES:
        op.create_index(f"uq_{table}_uid", table, ["uid"], unique=True)
    _create_sync_tables()


def downgrade() -> None:
    op.drop_index("ix_sync_op_entity", table_name="sync_op")
    op.drop_index("ix_sync_op_pending", table_name="sync_op")
    op.drop_index("uq_replica_self", table_name="replica")
    for table in _SYNC_TABLES:
        op.drop_table(table)
    for table in UID_TABLES:
        op.drop_index(f"uq_{table}_uid", table_name=table)
        op.execute(f"ALTER TABLE {_q(table)} DROP COLUMN uid")
