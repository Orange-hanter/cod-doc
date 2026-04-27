"""WEB-001: smoke-тесты scaffold веб-фронтенда."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project


@pytest.fixture
def web_client(tmp_path: Path):
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    entry = ProjectEntry(name="demo", path=str(repo))

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps
    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_index_renders_project_list(web_client) -> None:
    client, entry = web_client
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert entry.name in r.text
    assert "Projects" in r.text
    # link to project page is present
    assert f'href="/p/{entry.name}"' in r.text
    # base layout is wired
    assert '<link rel="stylesheet" href="/static/app.css">' in r.text


def test_index_warns_when_unconfigured(tmp_path: Path) -> None:
    cfg = Config()  # no api_key
    cfg.add_project(ProjectEntry(name="p1", path=str(tmp_path)))

    import cod_doc.api.deps as deps
    deps.set_config(cfg)

    from cod_doc.api.server import app
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert "API-ключ не настроен" in r.text


def test_index_empty_when_no_projects(tmp_path: Path) -> None:
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")

    import cod_doc.api.deps as deps
    deps.set_config(cfg)

    from cod_doc.api.server import app
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert "No projects yet." in r.text


def test_static_app_css_served(web_client) -> None:
    client, _ = web_client
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")
    assert ".topbar" in r.text


# ── WEB-002: project detail page ────────────────────────────────────────────

def test_project_show_renders(web_client) -> None:
    client, entry = web_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # heading + breadcrumb
    assert f">{entry.name}<" in r.text
    # tabs are present and link to expected routes
    assert f'href="/p/{entry.name}/docs"' in r.text
    assert f'href="/p/{entry.name}/tasks"' in r.text
    assert f'href="/p/{entry.name}/plans"' in r.text
    assert f'href="/p/{entry.name}/revisions"' in r.text
    assert f'href="/p/{entry.name}/run"' in r.text
    # stats card labels
    assert "Tasks total" in r.text
    assert "Last run" in r.text


def test_project_show_404_unknown(web_client) -> None:
    client, _ = web_client
    r = client.get("/p/nope-doesnt-exist")
    assert r.status_code == 404


def test_project_show_master_preview_present(web_client) -> None:
    client, entry = web_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    # Project.init() created MASTER.md from the j2 template — should appear
    assert "md-preview" in r.text
    assert entry.name in r.text


def test_project_show_master_truncated(tmp_path: Path) -> None:
    repo = tmp_path / "big-master"
    repo.mkdir()
    entry = ProjectEntry(name="big", path=str(repo))

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps
    deps.set_config(cfg)

    Project(entry).init()
    # overwrite MASTER.md with > 80 lines
    long_master = "\n".join(f"line {i}" for i in range(120))
    entry.master_path.write_text(long_master, encoding="utf-8")

    from cod_doc.api.server import app
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert "Показаны первые строки" in r.text
    assert "line 0" in r.text
    assert "line 79" in r.text
    assert "line 80" not in r.text
