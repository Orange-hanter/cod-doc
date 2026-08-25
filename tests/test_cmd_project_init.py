"""SYM-001: `project add` / `project init` обязаны создавать БД, а не только файлы.

До фикса `project_service.init_project` (alembic + строка `project` +
дефолтные routines) вызывался единственно из web-роута `POST /p/{slug}/init`;
CLI создавал только файлы, и задокументированная последовательность
`project add → project init → import docs` падала на пустом SQLite
(`OperationalError: no such table: project`).

Ассерты — по состоянию ФС/БД, не по тексту вывода: rich красит вывод при
FORCE_COLOR в окружении, и строковые ассерты на styled-вывод ломаются.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path

from cod_doc.cli.cmd_project import project as project_group
from cod_doc.cli.task import task as task_group
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine


def _query_one(db_path: Path, sql: str) -> object:
    engine = make_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def test_project_add_creates_db_row_and_schema(tmp_path: Path) -> None:
    repo = tmp_path / "fresh-repo"
    repo.mkdir()
    cfg = Config()

    result = CliRunner().invoke(
        project_group, ["add", str(repo), "--name", "probe"], obj={"config": cfg}
    )
    assert result.exit_code == 0, result.output

    db_path = repo / ".cod-doc" / "state.db"
    assert db_path.exists(), "project add не создал state.db"
    assert _query_one(db_path, "SELECT slug FROM project") == "probe"
    assert _query_one(db_path, "SELECT count(*) FROM alembic_version") == 1
    # PCA-914: дефолтный routine бутстрапится вместе с проектом.
    assert (
        _query_one(db_path, "SELECT count(*) FROM routine WHERE name='approval_stale_default'") == 1
    )


def test_project_add_then_task_list_works_end_to_end(tmp_path: Path) -> None:
    """Ровно тот путь, что падал до фикса: свежий каталог → add → task list."""
    repo = tmp_path / "fresh-repo"
    repo.mkdir()
    cfg = Config()
    runner = CliRunner()

    add = runner.invoke(project_group, ["add", str(repo), "-n", "probe"], obj={"config": cfg})
    assert add.exit_code == 0, add.output

    listed = runner.invoke(task_group, ["list", "-p", "probe"], obj={"config": cfg})
    assert listed.exit_code == 0, listed.output


def test_project_init_bootstraps_db_for_registered_entry(tmp_path: Path) -> None:
    """`project init` на записи без БД создаёт её; повторный вызов идемпотентен."""
    repo = tmp_path / "registered-repo"
    repo.mkdir()
    cfg = Config()
    cfg.add_project(ProjectEntry(name="probe", path=str(repo)))
    runner = CliRunner()

    first = runner.invoke(project_group, ["init", "probe"], obj={"config": cfg})
    assert first.exit_code == 0, first.output
    db_path = repo / ".cod-doc" / "state.db"
    assert db_path.exists()
    assert _query_one(db_path, "SELECT slug FROM project") == "probe"

    second = runner.invoke(project_group, ["init", "probe"], obj={"config": cfg})
    assert second.exit_code == 0, second.output
    assert _query_one(db_path, "SELECT count(*) FROM project") == 1, "init не идемпотентен"
