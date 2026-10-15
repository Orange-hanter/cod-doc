"""CLO-002: миграция 0049 — uid во всех authored-таблицах и служебные таблицы.

Что тест обязан закрыть (RFC 29 §4, §10.1, §11.1):

1. Ничего не теряется. Миграция трогает ``document`` и таблицы с
   CASCADE-детьми; ``batch_alter_table`` пересоздал бы их, и DROP старой унёс бы
   секции и ссылки. На пустой БД это не видно, поэтому данные наливаются на
   предыдущей ревизии, и счётчики секций, ссылок, задач и рёбер сверяются до и
   после — и после обратного downgrade.
2. У каждой authored-строки непустой уникальный ``uid``: у рёбер — uuid5 по
   ``sync_registry.edge_uid`` от uid концов, у остальных — uuid7.
3. Шесть таблиц по DDL §10.1 и симметричный downgrade: схема после
   ``upgrade`` + ``downgrade`` совпадает со схемой предыдущей ревизии.
"""

from __future__ import annotations

import importlib
import re
import sqlite3
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from cod_doc.infra.sync_registry import (
    COMPOSITE_PK_TABLES,
    UID_TABLES,
    edge_uid,
    spec_for,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from types import ModuleType

_THIS = "0049_sync_identity"
_MIGRATIONS = Path(__file__).resolve().parents[2] / "cod_doc" / "infra" / "migrations"
_UUID7 = 7
_UUID5 = 5

_SYNC_TABLES = ("replica", "sync_op", "changeset", "sync_ref", "sync_lease", "entity_alias")


def _script(revision: str) -> ModuleType:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    script = ScriptDirectory.from_config(cfg).get_revision(revision)
    assert script is not None, f"ревизии {revision} нет"
    return script.module


def _down_revision(revision: str) -> str:
    """Предыдущая ревизия по цепочке alembic, а не захардкоженное имя."""
    down = _script(revision).down_revision
    assert isinstance(down, str), f"{revision}: down_revision {down!r} — не одна ревизия"
    return down


_PREVIOUS = _down_revision(_THIS)

#: Сущности, которые acceptance велит сверять поимённо: секции, ссылки, задачи,
#: рёбра; плюс остальные засеянные таблицы с CASCADE-родителями.
_COUNTED = (
    "document",
    "section",
    "link",
    "doc_comment",
    "doc_node",
    "plan",
    "plan_section",
    "task",
    "dependency",
    "affected_file",
    "task_document",
    "commit_link",
    "adr",
    "adr_topic",
    "adr_relation",
    "adr_supersedes",
    "adr_task",
    "user_story",
    "story_acceptance",
    "story_link",
    "scenario",
    "scenario_step",
    "scenario_link",
    "module",
    "module_code",
    "module_dependency",
    "open_question",
    "open_question_option",
    "open_question_link",
    "tag",
    "task_tag",
    "routine",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(sep=" ")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ident(name: str) -> str:
    """Имя таблицы/колонки строкой в SQL — только из схемы или реестра."""
    assert re.fullmatch(r"[a-z_]+", name), name
    return f'"{name}"'


def _insert(conn: sqlite3.Connection, table: str, **values: object) -> None:
    cols = ", ".join(_ident(c) for c in values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {_ident(table)} ({cols}) VALUES ({marks})", tuple(values.values()))


def _seed_documents(conn: sqlite3.Connection, now: str) -> None:
    _insert(conn, "doc_node", row_id=1, project_id=1, node_key="arch", title="A", position=0)
    _insert(
        conn,
        "document",
        row_id=1,
        project_id=1,
        doc_key="master",
        path="MASTER.md",
        type="master",
        status="active",
        title="Master",
        created=now,
        last_updated=now,
        node_id=1,
    )
    for row_id, anchor in ((1, "intro"), (2, "body")):
        _insert(
            conn,
            "section",
            row_id=row_id,
            document_id=1,
            anchor=anchor,
            heading=anchor,
            level=2,
            position=row_id,
            body="text",
            content_hash="h",
        )
    _insert(conn, "link", row_id=1, project_id=1, from_section_id=1, raw="[x](#body)", kind="doc")
    _insert(conn, "doc_comment", row_id=1, document_id=1, section_id=2, body="c")


def _seed_tasks(conn: sqlite3.Connection, now: str) -> None:
    _insert(conn, "plan", row_id=1, project_id=1, scope="s", created=now, last_updated=now)
    _insert(
        conn, "plan_section", row_id=1, plan_id=1, letter="A", title="T", slug="A-T", position=0
    )
    for row_id, task_id in ((1, "COD-001"), (2, "COD-002")):
        _insert(
            conn,
            "task",
            row_id=row_id,
            project_id=1,
            task_id=task_id,
            plan_id=1,
            section_id=1,
            title=task_id,
            status="todo",
            type="feature",
            priority="medium",
            created=now,
            last_updated=now,
        )
    _insert(conn, "dependency", row_id=1, from_task_id=2, to_task_id=1, kind="blocks")
    _insert(conn, "affected_file", row_id=1, task_id=1, path="cod_doc/x.py")
    _insert(conn, "task_document", row_id=1, task_id=1, key="notes", title="N")
    _insert(
        conn, "commit_link", row_id=1, project_id=1, task_id="COD-001", sha="a" * 40, short_sha="a"
    )
    _insert(conn, "tag", row_id=1, project_id=1, name="hub")
    _insert(conn, "task_tag", task_id=1, tag_id=1)
    _insert(conn, "routine", row_id=1, project_id=1, name="r", check_name="drift")


def _seed_adrs(conn: sqlite3.Connection, now: str) -> None:
    _insert(conn, "adr_topic", row_id=1, project_id=1, name="t", created=now, last_updated=now)
    for row_id in (1, 2):
        _insert(conn, "adr", row_id=row_id, project_id=1, adr_id=f"ADR-00{row_id}", title="a")
    _insert(conn, "adr_relation", row_id=1, from_id=1, to_id=2, kind="amends", at=now)
    _insert(conn, "adr_supersedes", row_id=1, superseding_id=2, superseded_id=1)
    _insert(conn, "adr_task", row_id=1, adr_row_id=1, task_id="COD-001", relation="implements")


def _seed_stories_and_scenarios(conn: sqlite3.Connection, now: str) -> None:
    _insert(
        conn,
        "user_story",
        row_id=1,
        project_id=1,
        story_id="US-001",
        persona="p",
        narrative="n",
        status="draft",
        priority="medium",
        created=now,
        last_updated=now,
    )
    _insert(conn, "story_acceptance", row_id=1, story_id=1, position=0, criterion="c")
    _insert(
        conn,
        "story_link",
        row_id=1,
        story_id=1,
        to_kind="task",
        to_ref="COD-001",
        relation="implements",
    )
    _insert(
        conn,
        "scenario",
        row_id=1,
        project_id=1,
        scenario_id="SCN-001",
        title="s",
        kind="happy",
        group_key="g",
        status="draft",
        provenance="human",
        preconditions="p",
        expected="e",
        position=0,
        author="human:t",
        created=now,
        last_updated=now,
    )
    _insert(conn, "scenario_step", row_id=1, scenario_row_id=1, position=0, text="go")
    _insert(
        conn,
        "scenario_link",
        row_id=1,
        scenario_row_id=1,
        to_kind="story",
        to_ref="US-001",
        relation="covers",
    )
    for row_id, module_id in ((1, "M1-auth"), (2, "M2-core")):
        _insert(
            conn,
            "module",
            row_id=row_id,
            project_id=1,
            module_id=module_id,
            name=module_id,
            status="active",
        )
    _insert(conn, "module_code", row_id=1, module_id=1, kind="source", path="cod_doc/auth.py")
    _insert(conn, "module_dependency", row_id=1, from_module=1, to_module=2, reason="r")


def _seed_questions(conn: sqlite3.Connection, now: str) -> None:
    _insert(
        conn,
        "open_question",
        row_id=1,
        project_id=1,
        question_id="Q-001",
        title="q",
        question="?",
        status="open",
        priority="medium",
        author="human:t",
        created=now,
        last_updated=now,
    )
    _insert(
        conn, "open_question_option", row_id=1, question_row_id=1, position=0, title="o", chosen=0
    )
    _insert(
        conn,
        "open_question_link",
        row_id=1,
        question_row_id=1,
        to_kind="task",
        to_ref="COD-001",
        relation="blocks",
    )


def _seed(db_path: Path) -> dict[str, int]:
    """Проект, документ с секциями и ссылкой, задачи с рёбрами, ADR, истории…

    Возвращает счётчики сразу после сида: ожидание выводится из самого сида.
    """
    now = _now()
    conn = _connect(db_path)
    try:
        _insert(
            conn,
            "project",
            row_id=1,
            slug="p1",
            title="P1",
            root_path="/tmp/p1",
            created=now,
            updated=now,
            config_json="{}",
        )
        _seed_documents(conn, now)
        _seed_tasks(conn, now)
        _seed_adrs(conn, now)
        _seed_stories_and_scenarios(conn, now)
        _seed_questions(conn, now)
        conn.commit()
    finally:
        conn.close()
    seeded = _counts(db_path)
    empty = sorted(t for t, n in seeded.items() if n == 0)
    assert not empty, f"_seed не наполнил {empty}: потеря строк там была бы не видна"
    return seeded


def _counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            t: int(conn.execute(f"SELECT count(*) FROM {_ident(t)}").fetchone()[0])
            for t in _COUNTED
        }
    finally:
        conn.close()


def _fk_violations(db_path: Path) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({_ident(table)})")}


def _clauses(sql: str) -> frozenset[str]:
    """Части CREATE на верхнем уровне скобок, без пробелов и без порядка.

    Порядок ограничений в пересозданной таблице зависит от хэш-сида процесса
    alembic (0047 пересоздаёт ``user_story`` через batch), а не от 0049.
    """
    flat = re.sub(r"\s+", "", sql)
    parts: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(flat):
        if ch == "(":
            depth += 1
            if depth == 1:
                parts.append(flat[: i + 1])  # заголовок «CREATE … (»
                start = i + 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                parts.append(flat[start:i])
                start = i
        elif ch == "," and depth == 1:
            parts.append(flat[start:i])
            start = i + 1
    parts.append(flat[start:])
    return frozenset(parts)


def _schema(db_path: Path) -> set[tuple[str, frozenset[str]]]:
    """Схема без форматирования: ``ADD``/``DROP COLUMN`` правят текст CREATE."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name != 'alembic_version'"
        ).fetchall()
    finally:
        conn.close()
    return {(name, _clauses(sql)) for name, sql in rows}


@pytest.fixture
def upgraded(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    db_path = tmp_path / "seeded.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    seeded = _seed(db_path)
    run_alembic("upgrade", _THIS, db_url=db_url)
    return db_path, seeded


def test_upgrade_keeps_rows(upgraded: tuple[Path, dict[str, int]]) -> None:
    db_path, seeded = upgraded
    assert _counts(db_path) == seeded
    assert _fk_violations(db_path) == []


def test_every_authored_row_has_unique_uid(upgraded: tuple[Path, dict[str, int]]) -> None:
    db_path, _ = upgraded
    conn = sqlite3.connect(db_path)
    try:
        for table in sorted(UID_TABLES):
            rows = conn.execute(f"SELECT row_id, uid FROM {_ident(table)}").fetchall()
            assert all(uid for _, uid in rows), f"{table}: строка без uid"
            assert len({uid for _, uid in rows}) == len(rows), f"{table}: uid повторяется"
            index = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (f"uq_{table}_uid",),
            ).fetchone()
            assert index is not None and "UNIQUE" in index[0], table
            expected = _UUID5 if spec_for(table).deterministic_uid else _UUID7
            for _, uid in rows:
                assert uuid.UUID(uid).version == expected, (table, uid)
        for table in COMPOSITE_PK_TABLES:
            assert "uid" not in _columns(conn, table), table
    finally:
        conn.close()


def _endpoint_uids(conn: sqlite3.Connection, table: str, row_id: int) -> dict[str, object]:
    """Строка ребра с FK ключа, переведёнными в uid концов (вход ``edge_uid``)."""
    columns = spec_for(table).deterministic_uid
    assert columns is not None
    values = dict(
        zip(
            columns,
            conn.execute(
                f"SELECT {', '.join(_ident(c) for c in columns)} FROM {_ident(table)}"
                " WHERE row_id = ?",
                (row_id,),
            ).fetchone(),
            strict=True,
        )
    )
    for fk in conn.execute(f"PRAGMA foreign_key_list({_ident(table)})"):
        target, column = fk[2], fk[3]
        if column in values:
            (values[column],) = conn.execute(
                f"SELECT uid FROM {_ident(target)} WHERE row_id = ?", (values[column],)
            ).fetchone()
    return values


def test_edge_uid_matches_registry(upgraded: tuple[Path, dict[str, int]]) -> None:
    """Бэкфилл рёбер считает ту же формулу, что захват (``sync_registry.edge_uid``)."""
    db_path, _ = upgraded
    conn = sqlite3.connect(db_path)
    try:
        edges = sorted(t for t in UID_TABLES if spec_for(t).deterministic_uid)
        assert edges, "в реестре нет рёбер с uid"
        for table in edges:
            for row_id, uid in conn.execute(f"SELECT row_id, uid FROM {_ident(table)}"):
                assert uid == edge_uid(table, _endpoint_uids(conn, table, row_id)), table
    finally:
        conn.close()


def test_migration_freezes_registry_contract() -> None:
    """Миграция — застывший снимок: её литералы совпадают с реестром на момент 0049.

    Расхождение значит, что реестр поменял формулу или состав uid-таблиц:
    уже выданные uid рёбер с ним не сойдутся.
    """
    module = _script(_THIS)
    assert frozenset(module.UID_TABLES) == UID_TABLES
    assert {t: cols for t, (cols, _) in module.EDGE_KEYS.items()} == {
        t: spec_for(t).deterministic_uid for t in UID_TABLES if spec_for(t).deterministic_uid
    }
    assert (
        str(importlib.import_module("cod_doc.infra.sync_registry").EDGE_UID_NAMESPACE)
        == module.EDGE_UID_NAMESPACE
    )


def test_sync_tables_per_ddl(upgraded: tuple[Path, dict[str, int]]) -> None:
    db_path, _ = upgraded
    now = _now()
    conn = _connect(db_path)
    try:
        for table in _SYNC_TABLES:
            assert _columns(conn, table), table
        assert "replica_uid" in _columns(conn, "sync_op")
        _insert(conn, "replica", uid="r1", project_id=1, is_self=1, created=now)
        _insert(conn, "replica", uid="r2", project_id=1, is_self=0, created=now)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "replica", uid="r3", project_id=1, is_self=1, created=now)
        op = {
            "project_id": 1,
            "replica_uid": "r1",
            "hlc": "1",
            "entity_kind": "task",
            "entity_uid": "u",
            "fields": "{}",
            "base": "{}",
            "author": "human:t",
        }
        _insert(conn, "sync_op", op_id="o1", kind="upsert", **op)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "sync_op", op_id="o2", kind="merge", **op)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(
                conn,
                "entity_alias",
                project_id=1,
                entity_kind="task",
                alias="ADO-~k3f2",
                uid="u",
                reason="renamed",
                created=now,
            )
        indexes = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        assert {"uq_replica_self", "ix_sync_op_pending", "ix_sync_op_entity"} <= indexes
    finally:
        conn.close()


def test_downgrade_is_symmetric_and_keeps_data(
    upgraded: tuple[Path, dict[str, int]], tmp_path: Path
) -> None:
    db_path, seeded = upgraded
    reference = tmp_path / "reference.db"
    run_alembic("upgrade", _PREVIOUS, db_url=f"sqlite:///{reference}")

    run_alembic("downgrade", _PREVIOUS, db_url=f"sqlite:///{db_path}")

    assert _counts(db_path) == seeded
    assert _fk_violations(db_path) == []
    assert _schema(db_path) == _schema(reference)


def test_duplicate_edge_refuses_upgrade(tmp_path: Path) -> None:
    """``story_link`` без UNIQUE: дубль ребра получил бы один uid — миграция отказывает.

    Удалять строку молча миграция не вправе; отказ называет таблицу.
    """
    db_path = tmp_path / "dups.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    conn = _connect(db_path)
    try:
        _insert(
            conn,
            "story_link",
            row_id=2,
            story_id=1,
            to_kind="task",
            to_ref="COD-001",
            relation="implements",
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_alembic("upgrade", _THIS, db_url=db_url)
    assert b"story_link" in (excinfo.value.stderr or b"")
    conn = sqlite3.connect(db_path)
    try:
        assert "uid" not in _columns(conn, "task")
        (version,) = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version == _PREVIOUS
    finally:
        conn.close()
