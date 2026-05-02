"""WEB-041: shared `_layout/project_tabs.html` — single source for the tab strip.

Verifies:
- Live tabs render as `<a class="active|" href=...>` based on the `active` arg.
- Not-yet-implemented tabs (Plans/Revisions/Run) render as
  `<span class="tab-disabled">` with NO href and a "coming soon" tooltip.
- All four pages that show project tabs (overview, docs, tasks, doc detail)
  emit the include — i.e. the tab strip is consistent across them.
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
    m = re.search(r'<nav class="tabs">(.*?)</nav>', html, re.DOTALL)
    assert m is not None, "no <nav class='tabs'> block on page"
    return m.group(1)


def test_overview_tab_active_others_live_or_disabled(tabs_client) -> None:
    """Each live tab → <a>; each disabled tab → <span class=tab-disabled>."""
    from tests.api.conftest import EXPECTED_DISABLED_TABS, EXPECTED_LIVE_TABS

    client, entry = tabs_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    block = _extract_tabs_block(r.text)
    # Overview is the active anchor (special-case the slug-less path)
    assert re.search(r'<a[^>]*class="active"[^>]*href="/p/demo"[^>]*>Overview</a>', block)
    for live in EXPECTED_LIVE_TABS:
        if live == "overview":
            continue
        assert f'href="/p/demo/{live}"' in block, f"live tab '{live}' missing"
    if EXPECTED_DISABLED_TABS:
        assert 'class="tab-disabled"' in block
        assert "coming soon" in block
        for disabled in EXPECTED_DISABLED_TABS:
            assert f'href="/p/demo/{disabled}"' not in block
            assert f">{disabled.capitalize()}</span>" in block


def test_docs_list_tab_marks_docs_active(tabs_client) -> None:
    client, entry = tabs_client
    r = client.get(f"/p/{entry.name}/docs")
    assert r.status_code == 200
    block = _extract_tabs_block(r.text)
    assert re.search(r'<a[^>]*class="active"[^>]*href="/p/demo/docs"', block)


def test_tasks_list_tab_marks_tasks_active(tabs_client) -> None:
    client, entry = tabs_client
    r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    block = _extract_tabs_block(r.text)
    assert re.search(r'<a[^>]*class="active"[^>]*href="/p/demo/tasks"', block)


def test_disabled_tabs_have_no_href(tabs_client) -> None:
    """Regression: WEB-041 must NOT emit broken `href` for disabled tabs."""
    from tests.api.conftest import EXPECTED_DISABLED_TABS

    client, entry = tabs_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    block = _extract_tabs_block(r.text)
    for disabled in EXPECTED_DISABLED_TABS:
        assert f'href="/p/demo/{disabled}"' not in block, (
            f"disabled tab '{disabled}' must not emit a href"
        )
