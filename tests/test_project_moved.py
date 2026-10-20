"""Переехавший проект (``moved_to``): локальные поверхности не открывают его БД.

Проект ведётся на другом узле (Гермес), локальная ``state.db`` — откат, который
больше не обновляется. Правка через локальный CLI/MCP молча разошлась бы с узлом,
поэтому резолв БД отказывает, а обходы реестра проект пропускают.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config, ProjectEntry, ProjectMovedError

if TYPE_CHECKING:
    from pathlib import Path

NODE = "https://cod-doc.example.dev/mcp"


def _register(tmp_path: Path, *, moved: bool) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    entry = ProjectEntry(name="demo", path=str(root), moved_to=NODE if moved else None)
    Config().add_project(entry)
    Config.clear_cache()
    return root


def test_db_resolution_refuses_moved_project(tmp_path: Path) -> None:
    from cod_doc.infra.db import db_for_entry

    root = _register(tmp_path, moved=True)
    entry = Config.load().get_project("demo")
    assert entry is not None
    with pytest.raises(ProjectMovedError, match="переехал"):
        db_for_entry(entry)
    assert not (root / ".cod-doc" / "state.db").exists()  # пустую БД не создали


def test_cli_prints_error_not_traceback(tmp_path: Path) -> None:
    _register(tmp_path, moved=True)
    result = CliRunner().invoke(main, ["task", "list", "-p", "demo"])
    assert result.exit_code == 1
    assert NODE in result.output
    assert "Traceback" not in result.output
    assert not isinstance(result.exception, ProjectMovedError)


def test_registry_walks_skip_moved_project(tmp_path: Path) -> None:
    _register(tmp_path, moved=True)
    cfg = Config.load()
    assert [e.name for e in cfg.list_projects()] == []
    assert [e.name for e in cfg.list_projects(include_moved=True)] == ["demo"]


def test_project_list_shows_where_it_moved(tmp_path: Path) -> None:
    _register(tmp_path, moved=True)
    out = CliRunner().invoke(main, ["project", "list", "--json"]).output
    assert json.loads(out) == [
        {"slug": "demo", "root_path": str(tmp_path / "repo"), "db_url": None, "moved_to": NODE}
    ]


def test_not_moved_project_unchanged(tmp_path: Path) -> None:
    from cod_doc.infra.db import db_url_for_entry

    root = _register(tmp_path, moved=False)
    entry = Config.load().get_project("demo")
    assert entry is not None
    assert db_url_for_entry(entry).endswith(f"{root.name}/.cod-doc/state.db")
