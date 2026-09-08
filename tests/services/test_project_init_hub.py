"""STO-027: `init_project` мигрирует ту БД, которую откроет `db_for_entry`.

До правки шаг 2 (alembic upgrade) резолвил БД жёстко как
`<root>/.cod-doc/state.db`, а шаг 3 открывал её через `db_for_entry`, то есть
уже с учётом `db_url`. Для hub-проекта это означало: в рабочем дереве
появляется лишний пустой `state.db`, а сама hub-БД остаётся ненакатанной —
и шаг 3 падает на сверке alembic-головы.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from cod_doc.config import ProjectEntry
from cod_doc.infra.db import make_engine
from cod_doc.services.project_service import init_project

if TYPE_CHECKING:
    from pathlib import Path


def _scalar(db_path: Path, sql: str) -> object:
    engine = make_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def test_init_project_hub_migrates_db_url(tmp_path: Path) -> None:
    """Схема и строка проекта уезжают в hub-БД, embedded-файл не создаётся."""
    repo = tmp_path / "repo"
    repo.mkdir()
    hub_db = tmp_path / "hub" / "state.db"
    entry = ProjectEntry(name="hubbed", path=str(repo), db_url=f"sqlite:///{hub_db}")

    result = init_project(entry)

    assert hub_db.exists(), "init не создал hub-БД"
    assert _scalar(hub_db, "SELECT count(*) FROM alembic_version") == 1
    assert _scalar(hub_db, "SELECT slug FROM project") == "hubbed"
    assert not (repo / ".cod-doc" / "state.db").exists(), (
        "init создал лишний embedded state.db в рабочем дереве"
    )
    assert result.db_existed is False


def test_init_project_hub_is_idempotent(tmp_path: Path) -> None:
    """Повторный init по той же hub-БД не дублирует строку проекта."""
    repo = tmp_path / "repo"
    repo.mkdir()
    hub_db = tmp_path / "hub" / "state.db"
    entry = ProjectEntry(name="hubbed", path=str(repo), db_url=f"sqlite:///{hub_db}")

    init_project(entry)
    second = init_project(entry)

    assert second.db_existed is True
    assert second.db_row_existed is True
    assert _scalar(hub_db, "SELECT count(*) FROM project") == 1
    assert not (repo / ".cod-doc" / "state.db").exists()


def test_init_project_embedded_unchanged(tmp_path: Path) -> None:
    """Без `db_url` поведение прежнее: БД в `.cod-doc/` рабочего дерева."""
    repo = tmp_path / "repo"
    repo.mkdir()
    entry = ProjectEntry(name="plain", path=str(repo))

    result = init_project(entry)

    embedded = repo / ".cod-doc" / "state.db"
    assert embedded.exists()
    assert _scalar(embedded, "SELECT slug FROM project") == "plain"
    assert result.db_existed is False
