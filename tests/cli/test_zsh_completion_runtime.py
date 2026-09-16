"""Runtime-поведение `prelude.zsh`: запускаем его настоящим zsh.

Юнит-тесты проверяют ТЕКСТ дополнения, а здесь — что оно делает, когда его
исполняют. Повод конкретный: WAL-базу без файла `-shm` рядом нельзя открыть
только на чтение (SQLITE_CANTOPEN), и без фолбэка дополнение молча пустеет на
любом спокойном проекте. Ни `zsh -n`, ни проверка SQL по схеме этого не видят.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from typing import TYPE_CHECKING

import pytest

from cod_doc.cli.completion import PRELUDE_PATH

if TYPE_CHECKING:
    from pathlib import Path

_TIMEOUT = 60

pytestmark = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh не установлен")


def _wal_db_without_sidecars(root: Path) -> Path:
    """БД в WAL-режиме, закрытая чисто: `-wal` и `-shm` рядом отсутствуют."""
    db = root / "proj" / ".cod-doc" / "state.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("pragma journal_mode=wal")
    conn.execute("create table project(row_id integer primary key, slug text)")
    conn.execute("insert into project(slug) values ('proj')")
    conn.commit()
    conn.close()
    assert sorted(p.name for p in db.parent.iterdir()) == ["state.db"], (
        "sidecar'ы должны были исчезнуть при чистом закрытии"
    )
    return db


def _run_prelude(home: Path, snippet: str, db_root: Path) -> subprocess.CompletedProcess[str]:
    script = PRELUDE_PATH.read_text(encoding="utf-8") + snippet
    return subprocess.run(
        ["zsh", "-c", script],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
        cwd=str(db_root),
        env={"HOME": str(home), "COD_DOC_HOME": str(home / ".cod-doc"), "PATH": "/usr/bin:/bin"},
    )


@pytest.fixture
def registry_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".cod-doc").mkdir(parents=True)
    (home / ".cod-doc" / "config.yaml").write_text(
        f"projects:\n- name: proj\n  path: {tmp_path / 'proj'}\n  db_url: null\n",
        encoding="utf-8",
    )
    return home


_PROBE = """
typeset -A opt_args
opt_args[--project]='proj'
typeset -a words line
words=(cod-doc task show)
_cod_doc_sql "select slug from project;" || print -r -- "SQL-FAILED"
"""


def test_sql_works_on_wal_db_without_shm(tmp_path: Path, registry_home: Path) -> None:
    """Главный кейс: без фолбэка здесь был бы тихий пустой результат."""
    _wal_db_without_sidecars(tmp_path)
    proc = _run_prelude(registry_home, _PROBE, tmp_path)
    assert "SQL-FAILED" not in proc.stdout, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "proj", f"stdout={proc.stdout!r} stderr={proc.stderr!r}"


def test_readonly_path_is_tried_first(tmp_path: Path, registry_home: Path) -> None:
    """Когда `-shm` на месте, хватает первого — read-only — захода.

    Прямой признак: после запроса БД не обзавелась ничем новым сверх того,
    что уже лежало рядом.
    """
    db = _wal_db_without_sidecars(tmp_path)
    warm = sqlite3.connect(db)  # держим соединение -> sidecar'ы существуют
    warm.execute("select 1").fetchone()
    before = sorted(p.name for p in db.parent.iterdir())
    assert "state.db-shm" in before

    proc = _run_prelude(registry_home, _PROBE, tmp_path)
    after = sorted(p.name for p in db.parent.iterdir())
    # Закрываем только ПОСЛЕ снимка: чистое закрытие последнего соединения
    # само удаляет sidecar'ы и сделало бы сравнение бессмысленным.
    warm.close()
    assert proc.stdout.strip() == "proj", proc.stdout + proc.stderr
    assert after == before


def test_missing_database_is_silent(tmp_path: Path, registry_home: Path) -> None:
    """Нет файла БД — ни вывода, ни ругани в stderr."""
    (tmp_path / "proj" / ".cod-doc").mkdir(parents=True)
    proc = _run_prelude(registry_home, _PROBE, tmp_path)
    assert proc.stdout.strip() == "SQL-FAILED"
    assert proc.stderr.strip() == "", proc.stderr


def test_registry_parses_real_yaml_shape(tmp_path: Path, registry_home: Path) -> None:
    """Разбор реестра: имя, путь и db_url=null в виде name<TAB>path<TAB>''."""
    _wal_db_without_sidecars(tmp_path)
    proc = _run_prelude(registry_home, "\n_cod_doc_registry\n", tmp_path)
    # Без .strip(): db_url=null даёт ПУСТОЕ третье поле, и завершающий таб —
    # часть контракта, по нему `_cod_doc_db` отличает embedded от явного URL.
    assert proc.stdout.splitlines() == [f"proj\t{tmp_path / 'proj'}\t"]


def test_postgres_project_is_skipped(tmp_path: Path) -> None:
    """Непустой не-sqlite db_url -> дополнять нечем, молчим."""
    home = tmp_path / "home"
    (home / ".cod-doc").mkdir(parents=True)
    (home / ".cod-doc" / "config.yaml").write_text(
        "projects:\n- name: proj\n  path: /nonexistent\n"
        "  db_url: postgresql+psycopg://u@localhost/hub\n",
        encoding="utf-8",
    )
    proc = _run_prelude(home, _PROBE, tmp_path)
    assert proc.stdout.strip() == "SQL-FAILED"
    assert proc.stderr.strip() == "", proc.stderr
