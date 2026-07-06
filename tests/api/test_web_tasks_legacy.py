"""COD-058: legacy YAML tasks list page (/p/{slug}/tasks/legacy)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import re

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project, Task, TaskStatus

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def legacy_client(tmp_path: Path):
    repo = tmp_path / "demo-legacy"
    repo.mkdir()
    entry = ProjectEntry(name="demo", path=str(repo))

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    proj = Project(entry)
    proj.init()
    # Seed a few legacy YAML tasks with mixed statuses + priorities.
    proj.add_task(Task(title="Legacy alpha", priority=1, status=TaskStatus.PENDING))
    proj.add_task(Task(title="Legacy beta", priority=2, status=TaskStatus.IN_PROGRESS))
    proj.add_task(Task(title="Legacy gamma", priority=3, status=TaskStatus.DONE))
    proj.add_task(Task(title="Legacy delta", priority=4, status=TaskStatus.BLOCKED))

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_legacy_list_renders_all(legacy_client) -> None:
    client, entry = legacy_client
    r = client.get(f"/p/{entry.name}/tasks/legacy")
    assert r.status_code == 200
    assert "Legacy alpha" in r.text
    assert "Legacy beta" in r.text
    assert "Legacy gamma" in r.text
    assert "Legacy delta" in r.text
    # Crumbs/heading
    assert "Legacy YAML tasks" in r.text
    # Tabs present, Tasks tab active (legacy lives under it)
    assert re.search(
        r'href="/p/demo/tasks"[^>]*\bactive\b|class="[^"]*\bactive\b[^"]*"[^>]*href="/p/demo/tasks"',
        r.text,
    )
    # Footer count
    assert "of 4" in r.text


def test_legacy_list_status_filter(legacy_client) -> None:
    client, entry = legacy_client
    r = client.get(f"/p/{entry.name}/tasks/legacy?status=done")
    assert r.status_code == 200
    assert "Legacy gamma" in r.text
    assert "Legacy alpha" not in r.text
    assert "Legacy beta" not in r.text


def test_legacy_list_invalid_status_warns(legacy_client) -> None:
    client, entry = legacy_client
    r = client.get(f"/p/{entry.name}/tasks/legacy?status=garbage")
    assert r.status_code == 200
    assert "Неизвестное значение status" in r.text
    # falls back to all
    assert "Legacy alpha" in r.text


def test_legacy_list_pagination_clamps_limit(legacy_client) -> None:
    client, entry = legacy_client
    r = client.get(f"/p/{entry.name}/tasks/legacy?limit=2&offset=0")
    assert r.status_code == 200
    # First page (priority order: 1, 2, 3, 4 → alpha, beta, gamma, delta)
    assert "Legacy alpha" in r.text
    assert "Legacy beta" in r.text
    assert "Legacy gamma" not in r.text
    # Pager with "next →" present
    assert "next →" in r.text
    # 4 total, showing 1-2
    assert "of 4" in r.text


def test_legacy_list_pagination_offset_page_two(legacy_client) -> None:
    client, entry = legacy_client
    r = client.get(f"/p/{entry.name}/tasks/legacy?limit=2&offset=2")
    assert r.status_code == 200
    assert "Legacy gamma" in r.text
    assert "Legacy delta" in r.text
    assert "Legacy alpha" not in r.text
    # Pager with prev link, no next
    assert "← prev" in r.text


def test_legacy_list_empty_yaml(tmp_path: Path) -> None:
    repo = tmp_path / "no-legacy"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()  # Creates empty tasks.yaml

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/tasks/legacy")
    assert r.status_code == 200
    assert "нет legacy yaml-задач" in r.text.lower()


def test_legacy_list_404_unknown_project(legacy_client) -> None:
    client, _ = legacy_client
    r = client.get("/p/nope/tasks/legacy")
    assert r.status_code == 404


def test_main_tasks_page_links_to_legacy_when_yaml_present(legacy_client) -> None:
    """The DB-tasks page surfaces a one-line link if legacy YAML has rows."""
    client, entry = legacy_client
    r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    assert "Legacy YAML tasks (4)" in r.text
    assert f'href="/p/{entry.name}/tasks/legacy"' in r.text


def test_main_tasks_page_no_legacy_link_when_yaml_empty(tmp_path: Path) -> None:
    repo = tmp_path / "no-legacy-link"
    repo.mkdir()
    entry = ProjectEntry(name="bare2", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    assert "Legacy YAML tasks" not in r.text
