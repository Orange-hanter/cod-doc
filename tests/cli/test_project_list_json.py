"""AFT-017: `project list --json` — машинный вывод реестра проектов.

RFC 27 F15: плагинные скиллы и `cod-doc-env.sh` узнавали слаг проекта через
`sqlite3 -readonly .cod-doc/state.db`, потому что у `project list` не было
машинного вывода. Флаг печатает `[{slug, root_path, db_url}]` через
`click.echo(json.dumps(...))` (ADO-176) и не трогает БД проектов.

Эталоны — литералы из сида: ожидание, построенное вызовом
`db_url_for_entry`/`Config.list_projects`, проверяло бы код им самим.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config, ProjectEntry

if TYPE_CHECKING:
    from pathlib import Path

_BETA_DB_URL = "postgresql://u@localhost:5432/beta_lit"


@pytest.fixture
def no_force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """FORCE_COLOR из окружения разработчика не должен портить разбор вывода."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def _seed_registry(tmp_path: Path) -> tuple[Path, Path]:
    """Два проекта: alpha (embedded) и beta (литеральный hub db_url).

    Корни резолвим заранее: на macOS tmp_path живёт под /var → /private/var,
    а `db_url_for_entry` резолвит путь — литерал обязан совпасть с entry.path.
    """
    alpha_root = (tmp_path / "alpha_proj").resolve()
    alpha_root.mkdir()
    beta_root = (tmp_path / "beta_proj").resolve()
    beta_root.mkdir()
    cfg = Config.load()
    cfg.add_project(ProjectEntry(name="alpha", path=str(alpha_root)))
    cfg.add_project(ProjectEntry(name="beta", path=str(beta_root), db_url=_BETA_DB_URL))
    return alpha_root, beta_root


def _list_json() -> list[dict[str, str]]:
    result = CliRunner().invoke(main, ["project", "list", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert isinstance(rows, list)
    return rows


@pytest.mark.usefixtures("no_force_color")
def test_project_list_json_two_projects(tmp_path: Path) -> None:
    """Два проекта → list из 2 элементов с ключами {slug, root_path, db_url}."""
    _seed_registry(tmp_path)
    rows = _list_json()
    assert len(rows) == 2
    for row in rows:
        assert set(row.keys()) == {"slug", "root_path", "db_url"}
    assert {row["slug"] for row in rows} == {"alpha", "beta"}


@pytest.mark.usefixtures("no_force_color")
def test_project_list_json_values(tmp_path: Path) -> None:
    """root_path — каталог сида; db_url — литерал сида / embedded по корню."""
    alpha_root, beta_root = _seed_registry(tmp_path)
    rows = {row["slug"]: row for row in _list_json()}

    assert rows["alpha"]["root_path"] == str(alpha_root)
    assert rows["beta"]["root_path"] == str(beta_root)
    assert rows["beta"]["db_url"] == _BETA_DB_URL
    assert rows["alpha"]["db_url"] == f"sqlite:///{alpha_root}/.cod-doc/state.db"


@pytest.mark.usefixtures("no_force_color")
def test_project_list_json_empty_registry() -> None:
    """Пустой реестр + --json → `[]`, а не жёлтая подсказка."""
    rows = _list_json()
    assert rows == []


@pytest.mark.usefixtures("no_force_color")
def test_project_list_table_unchanged(tmp_path: Path) -> None:
    """Без --json — та же таблица: заголовок, оба слага, вывод не JSON.

    Оба проекта embedded: табличная ветка открывает БД каждого проекта
    (`project_stats`), и postgres-литерал без драйвера ронял бы её — это
    поведение до AFT-017, здесь не меняется.
    """
    alpha_root = (tmp_path / "alpha_proj").resolve()
    alpha_root.mkdir()
    beta_root = (tmp_path / "beta_proj").resolve()
    beta_root.mkdir()
    cfg = Config.load()
    cfg.add_project(ProjectEntry(name="alpha", path=str(alpha_root)))
    cfg.add_project(ProjectEntry(name="beta", path=str(beta_root)))
    result = CliRunner().invoke(main, ["project", "list"])
    assert result.exit_code == 0, result.output
    assert "Проекты COD-DOC" in result.output
    assert "alpha" in result.output
    assert "beta" in result.output
    with pytest.raises(ValueError):
        json.loads(result.output)
