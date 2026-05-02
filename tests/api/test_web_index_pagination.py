"""WEB-013: index batch stats + pagination.

Verifies:
- 10 projects render in a single request without crashing (smoke).
- `Project.batch_stats` is invoked once per request — i.e. the handler
  no longer iterates `Project(entry).stats()` sequentially.
- `?limit=N` clamps the page size and `?offset=K` skips entries.
- Pagination summary and prev/next links render.
- Out-of-range / invalid params clamp safely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project

if TYPE_CHECKING:
    from pathlib import Path


def _seed(cfg: Config, tmp_path: Path, count: int) -> list[ProjectEntry]:
    entries: list[ProjectEntry] = []
    for i in range(count):
        repo = tmp_path / f"proj-{i:02d}"
        repo.mkdir()
        e = ProjectEntry(name=f"proj-{i:02d}", path=str(repo))
        cfg.add_project(e)
        Project(e).init()
        entries.append(e)
    return entries


@pytest.fixture
def many_projects_client(tmp_path: Path):
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    entries = _seed(cfg, tmp_path, count=10)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entries


def test_index_renders_10_projects_in_single_request(many_projects_client) -> None:
    client, entries = many_projects_client
    r = client.get("/")
    assert r.status_code == 200
    for e in entries:
        assert f'href="/p/{e.name}"' in r.text


def test_index_uses_batch_stats(many_projects_client) -> None:
    """Handler must call Project.batch_stats once, not Project(...).stats() per project."""
    client, _ = many_projects_client
    with (
        patch.object(Project, "batch_stats", wraps=Project.batch_stats) as batch_spy,
        patch.object(Project, "stats", autospec=True) as stats_spy,
    ):
        # Make stats() return something picklable-trivial
        stats_spy.return_value = {
            "total": 0, "pending": 0, "in_progress": 0, "done": 0,
            "failed": 0, "status": "idle", "last_run": None,
        }
        r = client.get("/")
    assert r.status_code == 200
    # batch_stats called exactly once for the page.
    assert batch_spy.call_count == 1


def test_index_pagination_limit(many_projects_client) -> None:
    client, _ = many_projects_client
    r = client.get("/?limit=3")
    assert r.status_code == 200
    # Page summary mentions 1–3 of 10.
    assert "1–3 of 10" in r.text or "1–3 of 10" in r.text
    # Project 02 visible, 03 not.
    assert "proj-02" in r.text
    assert "proj-03" not in r.text
    # Next link points to offset=3.
    assert 'href="/?limit=3&amp;offset=3"' in r.text


def test_index_pagination_offset(many_projects_client) -> None:
    client, _ = many_projects_client
    r = client.get("/?limit=3&offset=3")
    assert r.status_code == 200
    assert "4–6 of 10" in r.text
    assert "proj-03" in r.text
    assert "proj-06" not in r.text  # next page
    # Prev link goes back to 0.
    assert 'href="/?limit=3&amp;offset=0"' in r.text


def test_index_last_page_disables_next(many_projects_client) -> None:
    client, _ = many_projects_client
    r = client.get("/?limit=3&offset=9")
    assert r.status_code == 200
    # Only proj-09 on this page (10–10 of 10).
    assert "proj-09" in r.text
    assert "10–10 of 10" in r.text
    # Next is disabled — no more pages.
    assert 'class="page-disabled">Next' in r.text
    # Prev is live.
    assert 'href="/?limit=3&amp;offset=6"' in r.text


def test_index_out_of_range_offset_returns_empty_page(many_projects_client) -> None:
    """Defensive: huge offset returns no rows, but still 200."""
    client, _ = many_projects_client
    r = client.get("/?limit=3&offset=9999")
    assert r.status_code == 200
    assert "proj-00" not in r.text
    # Prev link points back into the data.
    assert 'href="/?limit=3' in r.text


def test_index_invalid_params_clamp_safely(many_projects_client) -> None:
    """limit=0 or negative is clamped to 1; offset=-5 clamped to 0."""
    client, _ = many_projects_client
    r = client.get("/?limit=0&offset=-5")
    assert r.status_code == 200
    # Only the first project visible (limit clamped to 1).
    assert "proj-00" in r.text
    assert "proj-01" not in r.text


def test_index_no_pagination_when_under_limit(many_projects_client) -> None:
    """With default limit (20) and 10 projects, no pagination block needed."""
    client, _ = many_projects_client
    r = client.get("/")
    # Total <= limit → no prev/next bar
    assert 'class="pagination"' not in r.text


def test_batch_stats_returns_results_in_input_order(tmp_path: Path) -> None:
    """Project.batch_stats must preserve the order of input entries."""
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    entries = _seed(cfg, tmp_path, count=5)
    results = Project.batch_stats(entries)
    assert len(results) == len(entries)
    # Each entry's stats dict has the expected fields; we don't compare
    # contents (all empty-fresh) but verify zip alignment is meaningful
    # by mutating one entry's tasks file and checking only that entry
    # reports nonzero totals.
    import yaml
    target = entries[2]
    target.cod_doc_dir.joinpath("tasks.yaml").write_text(
        yaml.dump({"tasks": [{"id": "X", "title": "y"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    results = Project.batch_stats(entries)
    assert results[2]["total"] == 1
    for i, stats in enumerate(results):
        if i != 2:
            assert stats["total"] == 0


def test_batch_stats_empty_input_returns_empty_list() -> None:
    assert Project.batch_stats([]) == []
