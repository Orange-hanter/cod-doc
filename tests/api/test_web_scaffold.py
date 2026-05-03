"""WEB-001: smoke-тесты scaffold веб-фронтенда."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project

if TYPE_CHECKING:
    from pathlib import Path


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
    # base layout is wired (URL is versioned via WEB-051: /static/app.css?v=...)
    assert '<link rel="stylesheet" href="/static/app.css?v=' in r.text


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
    # app.css is a thin entry point that @imports the partials in css/.
    assert "@import" in r.text and "css/_base.css" in r.text
    # The original .topbar rule lives in the base partial — ensure it's
    # actually served (not just referenced).
    base = client.get("/static/css/_base.css")
    assert base.status_code == 200
    assert ".topbar" in base.text


# ── WEB-002: project detail page ────────────────────────────────────────────


def test_project_show_renders(web_client) -> None:
    """Tab strip drives off conftest constants — flip there when a tab goes live."""
    from tests.api.conftest import EXPECTED_DISABLED_TABS, EXPECTED_LIVE_TABS

    client, entry = web_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # heading + breadcrumb
    assert f">{entry.name}<" in r.text
    # live tabs render as anchors
    for live in EXPECTED_LIVE_TABS:
        if live == "overview":
            continue  # path is /p/{slug} (no trailing segment)
        assert f'href="/p/{entry.name}/{live}"' in r.text
    # disabled tabs → no href, label visible inside <span>
    if EXPECTED_DISABLED_TABS:
        assert 'class="tab-disabled"' in r.text
    for disabled in EXPECTED_DISABLED_TABS:
        assert f'href="/p/{entry.name}/{disabled}"' not in r.text
        assert f">{disabled.capitalize()}<" in r.text
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
    # MASTER.md now renders as markdown (not <pre>) — see request #3 (UI fix).
    assert 'class="master-body' in r.text
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


# ── WEB-051: static asset versioning ────────────────────────────────────


def test_static_url_appends_version_query() -> None:
    """`static_url('app.css')` must return /static/app.css?v=<hash>."""
    from cod_doc.api.web.templates_env import static_url

    url = static_url("app.css")
    assert url.startswith("/static/app.css?v=")
    # Sanity: the fingerprint segment is non-empty.
    assert len(url.split("?v=")[1]) > 0


def test_static_url_falls_back_for_missing_file() -> None:
    """A missing file shouldn't 500 the page render — drop the ?v= silently."""
    from cod_doc.api.web.templates_env import static_url

    url = static_url("does-not-exist-xyz.js")
    # Either no ?v= (graceful fallback) OR ?v= followed by empty (also OK).
    if "?v=" in url:
        assert url.endswith("?v=")
    else:
        assert url == "/static/does-not-exist-xyz.js"


# ── WEB-052 / SW-LO-3: missing MASTER.md doesn't crash project page ─────


def test_project_show_handles_missing_master(tmp_path: Path) -> None:
    """User can delete MASTER.md after init() — page still renders gracefully."""
    repo = tmp_path / "no-master"
    repo.mkdir()
    entry = ProjectEntry(name="hollow", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()
    # Delete MASTER.md after init (the user might do this by accident or design)
    entry.master_path.unlink()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    # The template shows the "ещё не создан" warning instead of crashing.
    assert "ещё не создан" in r.text
