"""Workspace-local project discovery for stale global registries."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from cod_doc.config import Config, ProjectEntry

if TYPE_CHECKING:
    from pathlib import Path


def _write_project_db(root: Path, *, slug: str = "cod-doc") -> None:
    db_path = root / ".cod-doc" / "state.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("create table project (slug text, root_path text)")
        conn.execute("insert into project values (?, ?)", (slug, str(root)))


def test_get_project_discovers_workspace_db(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_project_db(repo)
    monkeypatch.chdir(repo)

    entry = Config(projects=[]).get_project("cod-doc")

    assert entry is not None
    assert entry.name == "cod-doc"
    assert entry.root == repo.resolve()


def test_config_registry_wins_over_workspace_discovery(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    configured = tmp_path / "configured"
    repo.mkdir()
    configured.mkdir()
    _write_project_db(repo)
    monkeypatch.chdir(repo)

    cfg = Config(projects=[ProjectEntry(name="cod-doc", path=str(configured)).model_dump()])

    assert cfg.get_project("cod-doc").root == configured.resolve()


def test_get_project_does_not_return_wrong_workspace_slug(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_project_db(repo, slug="other")
    monkeypatch.chdir(repo)

    assert Config(projects=[]).get_project("cod-doc") is None


def test_list_projects_includes_workspace_db(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_project_db(repo)
    monkeypatch.chdir(repo)

    assert [entry.name for entry in Config(projects=[]).list_projects()] == ["cod-doc"]
