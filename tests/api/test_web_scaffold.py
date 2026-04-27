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
