"""CLO-017: миграция 0051 — таблица ``sync_conflict`` (RFC 29 §3.7, §10.1).

Что тест обязан закрыть (§11.1 п. 4):

1. Ничего не теряется. Данные — authored-строки, журнал ``sync_op`` и индекс
   ``changeset`` — наливаются на предыдущей ревизии; счётчики сверяются после
   upgrade и после обратного downgrade.
2. DDL по §3.7 плюс колонка ``kind`` из §10.1: ``CHECK`` на вид и статус,
   ``theirs_op`` обязателен, ``field`` и значения — nullable.
3. Downgrade симметричен: схема после ``upgrade`` + ``downgrade`` совпадает со
   схемой предыдущей ревизии.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from tests._alembic import run_alembic
from tests.infra.test_migration_0049_preserves_data import (
    _counts,
    _down_revision,
    _fk_violations,
    _insert,
    _schema,
    _seed,
)

if TYPE_CHECKING:
    from pathlib import Path

_THIS = "0051_sync_conflict"
_PREVIOUS = _down_revision(_THIS)
_JOURNAL = ("sync_op", "changeset", "replica")


def _journal_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in _JOURNAL}
    finally:
        conn.close()


def _seed_journal(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        _insert(conn, "replica", uid="r-1", project_id=1, is_self=1, created="2026-10-17 00:00:00")
        for i, changeset in enumerate(("c" * 64, None)):
            _insert(
                conn, "sync_op", op_id=f"op-{i}", project_id=1, replica_uid="r-1",
                hlc=f"{1760000000000 + i:013d}.0000.r-1", entity_kind="task",
                entity_uid="t-1", kind="upsert", fields='{"title":"x"}', base="{}",
                author="human:t", changeset=changeset,
            )  # fmt: skip
        _insert(
            conn, "changeset", sha="c" * 64, project_id=1, parents="[]", replica_uid="r-1",
            actor="human:t", epoch="0050_sync_rank", hlc_max="1760000000000.0000.r-1",
            digest="0" * 64, applied_at="2026-10-17 00:00:00",
        )  # fmt: skip
        conn.commit()
    finally:
        conn.close()


def _seeded_db(tmp_path: Path) -> tuple[Path, dict[str, int], dict[str, int]]:
    db_path = tmp_path / "seeded.db"
    run_alembic("upgrade", _PREVIOUS, db_url=f"sqlite:///{db_path}")
    _seed(db_path)
    _seed_journal(db_path)
    return db_path, _counts(db_path), _journal_counts(db_path)


@pytest.fixture
def upgraded(tmp_path: Path) -> tuple[Path, dict[str, int], dict[str, int]]:
    db_path, seeded, journal = _seeded_db(tmp_path)
    run_alembic("upgrade", _THIS, db_url=f"sqlite:///{db_path}")
    return db_path, seeded, journal


def test_upgrade_keeps_rows_and_journal(
    upgraded: tuple[Path, dict[str, int], dict[str, int]],
) -> None:
    db_path, seeded, journal = upgraded
    assert _counts(db_path) == seeded
    assert _journal_counts(db_path) == journal
    assert _fk_violations(db_path) == []


def test_sync_conflict_per_ddl(upgraded: tuple[Path, dict[str, int], dict[str, int]]) -> None:
    db_path, _, _ = upgraded
    conn = sqlite3.connect(db_path)
    try:
        info = {row[1]: row for row in conn.execute("PRAGMA table_info(sync_conflict)")}
        assert set(info) == {
            "uid", "project_id", "entity_kind", "entity_uid", "field", "kind", "ours",
            "theirs", "base", "theirs_op", "status", "resolved_by", "resolved_at",
        }  # fmt: skip
        required = {name for name, row in info.items() if row[3]}
        assert required == {
            "uid",
            "project_id", "entity_kind", "entity_uid", "kind", "theirs_op", "status",
        }  # fmt: skip
        assert info["uid"][5] == 1  # PK
        fks = [(fk[2], fk[3], fk[4], fk[6]) for fk in conn.execute(
            "PRAGMA foreign_key_list(sync_conflict)")]  # fmt: skip
        assert fks == [("project", "project_id", "row_id", "CASCADE")]

        def put(uid: str, kind: str, status: str = "open") -> None:
            conn.execute(
                "INSERT INTO sync_conflict (uid, project_id, entity_kind, entity_uid, kind, "
                "theirs_op, status) VALUES (?, 1, 'task', 't-1', ?, 'op-0', ?)",
                (uid, kind, status),
            )

        for i, kind in enumerate(
            ("field", "text", "status", "delete_edit", "key_merge", "double_checkout")
        ):
            put(f"k{i}", kind)
        with pytest.raises(sqlite3.IntegrityError, match="ck_sync_conflict_kind"):
            put("bad-kind", "lww")
        with pytest.raises(sqlite3.IntegrityError, match="ck_sync_conflict_status"):
            put("bad-status", "field", "closed")
    finally:
        conn.close()


def test_downgrade_is_symmetric_and_keeps_data(tmp_path: Path) -> None:
    db_path, seeded, journal = _seeded_db(tmp_path)
    before = _schema(db_path)
    url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _THIS, db_url=url)
    run_alembic("downgrade", _PREVIOUS, db_url=url)
    assert _schema(db_path) == before
    assert _counts(db_path) == seeded
    assert _journal_counts(db_path) == journal
    run_alembic("upgrade", _THIS, db_url=url)
    assert _counts(db_path) == seeded
