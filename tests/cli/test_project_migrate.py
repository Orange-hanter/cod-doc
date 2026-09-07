"""STO-009: `project migrate` мигрирует ту БД, которую откроет `db_for_entry`.

Контейнерный bootstrap (`entrypoint.sh`) резолвил БД через
`resolve_db_url(entry.root)`, то есть всегда по embedded-пути
`<root>/.cod-doc/state.db`, и на hub-проекте мигрировал не ту базу, печатая
`[migrate] ok`. Теперь bootstrap зовёт эту команду, а она — общий резолв.

Ассерты — по состоянию ФС/БД и exit-коду, не по тексту: rich переносит длинные
строки по ширине терминала, и подстроки с путями в них рвутся.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner
from sqlalchemy import text

from cod_doc.cli.cmd_project import project as project_group
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine

if TYPE_CHECKING:
    from pathlib import Path


def _scalar(db_path: Path, sql: str) -> object:
    engine = make_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def _config_with(*entries: ProjectEntry) -> Config:
    cfg = Config()
    for entry in entries:
        cfg.add_project(entry)
    return cfg


def test_migrate_hub_project_uses_db_url(tmp_path: Path) -> None:
    """Голова уезжает в hub-БД; embedded-файл в рабочем дереве не появляется."""
    repo = tmp_path / "repo"
    repo.mkdir()
    hub_db = tmp_path / "hub" / "state.db"
    cfg = _config_with(ProjectEntry(name="hubbed", path=str(repo), db_url=f"sqlite:///{hub_db}"))

    result = CliRunner().invoke(project_group, ["migrate", "hubbed"], obj={"config": cfg})

    assert result.exit_code == 0, result.output
    assert _scalar(hub_db, "SELECT count(*) FROM alembic_version") == 1
    assert not (repo / ".cod-doc" / "state.db").exists()


def test_migrate_embedded_project_unchanged(tmp_path: Path) -> None:
    """Без `db_url` мигрируется embedded-БД рабочего дерева."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _config_with(ProjectEntry(name="plain", path=str(repo)))

    result = CliRunner().invoke(project_group, ["migrate", "plain"], obj={"config": cfg})

    assert result.exit_code == 0, result.output
    assert _scalar(repo / ".cod-doc" / "state.db", "SELECT count(*) FROM alembic_version") == 1


def test_migrate_all_reports_each_project(tmp_path: Path) -> None:
    hub_db = tmp_path / "hub" / "state.db"
    first = tmp_path / "first"
    first.mkdir()
    second = tmp_path / "second"
    second.mkdir()
    cfg = _config_with(
        ProjectEntry(name="first", path=str(first)),
        ProjectEntry(name="second", path=str(second), db_url=f"sqlite:///{hub_db}"),
    )

    result = CliRunner().invoke(project_group, ["migrate", "--all"], obj={"config": cfg})

    assert result.exit_code == 0, result.output
    assert "first" in result.output
    assert "second" in result.output
    assert _scalar(first / ".cod-doc" / "state.db", "SELECT count(*) FROM alembic_version") == 1
    assert _scalar(hub_db, "SELECT count(*) FROM alembic_version") == 1


def test_migrate_all_survives_broken_entry_and_exits_nonzero(tmp_path: Path) -> None:
    """Одна битая запись не отменяет остальные, но код возврата ненулевой."""
    good = tmp_path / "good"
    good.mkdir()
    broken = tmp_path / "broken"
    broken.mkdir()
    cfg = _config_with(
        ProjectEntry(name="good", path=str(good)),
        ProjectEntry(name="broken", path=str(broken), db_url="не-урл-вовсе"),
    )

    result = CliRunner().invoke(project_group, ["migrate", "--all"], obj={"config": cfg})

    assert result.exit_code == 1, result.output
    assert "broken" in result.output
    assert _scalar(good / ".cod-doc" / "state.db", "SELECT count(*) FROM alembic_version") == 1


def test_migrate_requires_name_or_all(tmp_path: Path) -> None:
    cfg = _config_with(ProjectEntry(name="plain", path=str(tmp_path)))

    both = CliRunner().invoke(project_group, ["migrate", "plain", "--all"], obj={"config": cfg})
    neither = CliRunner().invoke(project_group, ["migrate"], obj={"config": cfg})

    assert both.exit_code == 2
    assert neither.exit_code == 2


def test_migrate_unknown_project(tmp_path: Path) -> None:
    cfg = _config_with(ProjectEntry(name="plain", path=str(tmp_path)))

    result = CliRunner().invoke(project_group, ["migrate", "nope"], obj={"config": cfg})

    assert result.exit_code == 1
