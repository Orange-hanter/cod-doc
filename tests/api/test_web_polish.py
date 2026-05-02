"""Polish tickets surfaced by the 2026-05-02 checkpoint audit:
- WEB-013b — clamp empty-page summary on `/`
- WEB-022b — log WebError events from web_error_handler
- WEB-054  — cap flash_message cookie length (truncate_for_cookie)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.api.web.errors import (
    COOKIE_FLASH_MAX_LEN,
    NotFoundWebError,
    truncate_for_cookie,
)
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project

if TYPE_CHECKING:
    from pathlib import Path


# ── WEB-054: truncate_for_cookie unit tests ──────────────────────────────


def test_truncate_short_message_unchanged() -> None:
    assert truncate_for_cookie("hello") == "hello"


def test_truncate_at_max_len_unchanged() -> None:
    msg = "x" * COOKIE_FLASH_MAX_LEN
    assert truncate_for_cookie(msg) == msg


def test_truncate_long_message_appends_ellipsis() -> None:
    long = "y" * (COOKIE_FLASH_MAX_LEN + 100)
    out = truncate_for_cookie(long)
    assert len(out) == COOKIE_FLASH_MAX_LEN
    assert out.endswith("…")
    assert out[:-1] == "y" * (COOKIE_FLASH_MAX_LEN - 1)


def test_truncate_custom_max_len() -> None:
    out = truncate_for_cookie("abcdefgh", max_len=4)
    assert len(out) == 4
    assert out.endswith("…")
    assert out == "abc…"


# ── WEB-022b: web_error_handler logs WebErrors ───────────────────────────


@pytest.fixture
def web_app_client(tmp_path: Path):
    """Minimal app with a route that always raises a WebError."""
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(ProjectEntry(name="demo", path=str(tmp_path)))

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(cfg.list_projects()[0]).init()

    # Register the route on the real app so we exercise the registered handler.
    from cod_doc.api.server import app

    if "/__test_raises__" not in {r.path for r in app.routes}:  # type: ignore[attr-defined]
        @app.get("/__test_raises__")
        def _probe() -> None:
            raise NotFoundWebError("test-not-found")

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_weberror_handler_emits_log_record(web_app_client, caplog) -> None:
    """The handler must log every WebError at INFO level."""
    caplog.set_level(logging.INFO, logger="cod_doc.api")
    r = web_app_client.get(
        "/__test_raises__",
        headers={"Referer": "http://testserver/somewhere"},
        follow_redirects=False,
    )
    assert r.status_code == 303  # form-post → redirect path
    matching = [rec for rec in caplog.records if "WebError" in rec.getMessage()]
    assert matching, "Expected a 'WebError' log record"
    record = matching[-1]
    assert "test-not-found" in record.getMessage()
    assert "/__test_raises__" in record.getMessage()
    assert "404" in record.getMessage()


def test_weberror_handler_truncates_flash_cookie(web_app_client) -> None:
    """A WebError with a huge message produces a bounded-length cookie."""
    huge = "Z" * 5000

    from cod_doc.api.server import app

    if "/__test_huge__" not in {r.path for r in app.routes}:  # type: ignore[attr-defined]
        @app.get("/__test_huge__")
        def _huge() -> None:
            raise NotFoundWebError(huge)

    r = web_app_client.get(
        "/__test_huge__",
        headers={"Referer": "http://testserver/x"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    cookies = r.headers.get_list("set-cookie")
    msg_cookie = next(c for c in cookies if c.startswith("flash_message="))
    value = msg_cookie.split("=", 1)[1].split(";", 1)[0]
    # Production uses default max_len=512; raw value is much smaller than input.
    assert len(value) < len(huge)
    # Worst-case percent-encoding triples each char; bound the raw cookie value.
    assert len(value) <= COOKIE_FLASH_MAX_LEN * 3
    # Truncation marker (percent-encoded ellipsis) present.
    assert "%E2%80%A6" in value  # …


# ── WEB-013b: empty-page summary clamp on / ──────────────────────────────


@pytest.fixture
def index_with_projects(tmp_path: Path):
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    for i in range(3):
        repo = tmp_path / f"p-{i}"
        repo.mkdir()
        e = ProjectEntry(name=f"p-{i}", path=str(repo))
        cfg.add_project(e)
        Project(e).init()

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_index_empty_page_shows_zero_zero_summary(index_with_projects) -> None:
    """With offset > total we used to show e.g. "4–3 of 3"; now shows "0–0 of 3"."""
    r = index_with_projects.get("/?limit=2&offset=99")
    assert r.status_code == 200
    assert "0–0 of 3" in r.text
    # No backwards range (regression guard).
    for bad in ("100–", "99–"):
        assert bad not in r.text


def test_index_normal_page_summary_unchanged(index_with_projects) -> None:
    """Non-empty page summary must still read e.g. "1–2 of 3"."""
    r = index_with_projects.get("/?limit=2&offset=0")
    assert r.status_code == 200
    assert "1–2 of 3" in r.text
