"""WEB-041: shared `_layout/project_tabs.html` — single source for the tab strip.

Verifies:
- Primary tabs render as `<a class="tab-link active?" href=...>` based on `active`.
- Secondary tabs live in the "More" dropdown and the wide-screen secondary row.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tabs_client(tmp_path: Path):
    repo = tmp_path / "tabs-demo"
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


def _extract_tabs_block(html: str) -> str:
    m = re.search(r'<nav class="tabs tabs-project"[^>]*>(.*?)</nav>', html, re.DOTALL)
    assert m is not None, "no project tabs <nav> block on page"
    return m.group(1)


def test_overview_tab_active_others_live_or_disabled(tabs_client) -> None:
    """Primary tabs are live anchors; overview is active on the hub page."""
    from tests.api.conftest import EXPECTED_LIVE_TABS

    client, entry = tabs_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    block = _extract_tabs_block(r.text)
    assert re.search(
        r'<a[^>]*class="[^"]*tab-link[^"]*active[^"]*"[^>]*href="/p/demo"[^>]*>.*?Overview',
        block,
        re.DOTALL,
    )
    for live in EXPECTED_LIVE_TABS:
        if live == "overview":
            continue
        assert f'href="/p/demo/{live}"' in block, f"live tab '{live}' missing"
    assert 'class="tabs-more"' in block


def test_docs_list_tab_marks_docs_active(tabs_client) -> None:
    client, entry = tabs_client
    r = client.get(f"/p/{entry.name}/docs")
    assert r.status_code == 200
    block = _extract_tabs_block(r.text)
    assert re.search(
        r'<a[^>]*class="[^"]*tab-link[^"]*active[^"]*"[^>]*href="/p/demo/docs"',
        block,
    )


def test_tasks_list_tab_marks_tasks_active(tabs_client) -> None:
    client, entry = tabs_client
    r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    block = _extract_tabs_block(r.text)
    assert re.search(
        r'<a[^>]*class="[^"]*tab-link[^"]*active[^"]*"[^>]*href="/p/demo/tasks"',
        block,
    )


def test_disabled_tabs_have_no_href(tabs_client) -> None:
    """Regression: disabled tabs must not emit broken hrefs."""
    from tests.api.conftest import EXPECTED_DISABLED_TABS

    client, entry = tabs_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    block = _extract_tabs_block(r.text)
    if not EXPECTED_DISABLED_TABS:
        assert 'class="tab-disabled"' not in block
        return
    assert 'class="tab-disabled"' in block
    for disabled in EXPECTED_DISABLED_TABS:
        assert f'href="/p/{entry.name}/{disabled}"' not in block
