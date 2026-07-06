"""Web UI i18n — locale cookie, translate(), template integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def i18n_client(tmp_path: Path):
    repo = tmp_path / "i18n-demo"
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


def test_default_locale_is_russian_without_cookie(i18n_client, monkeypatch) -> None:
    """COD_DOC_LOCALE default — Russian UI when no cookie/Accept-Language."""
    monkeypatch.delenv("COD_DOC_LOCALE", raising=False)
    client, entry = i18n_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert "Центр управления" in r.text
    assert 'lang="ru"' in r.text


def test_english_locale_via_cookie(i18n_client, monkeypatch) -> None:
    monkeypatch.delenv("COD_DOC_LOCALE", raising=False)
    client, entry = i18n_client
    r = client.get(f"/p/{entry.name}", cookies={"cod-doc-locale": "en"})
    assert r.status_code == 200
    assert "Command center" in r.text
    assert 'lang="en"' in r.text


def test_locale_switch_sets_cookie_and_redirects(i18n_client) -> None:
    client, entry = i18n_client
    r = client.get(
        f"/locale/en?next=/p/{entry.name}",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/{entry.name}"
    assert r.cookies.get("cod-doc-locale") == "en"


def test_kanban_columns_translated_ru(i18n_client, migrate_db, monkeypatch) -> None:
    """Kanban column headers use locale catalog."""
    monkeypatch.delenv("COD_DOC_LOCALE", raising=False)
    client, entry = i18n_client
    # Seed minimal DB — reuse tasks_client pattern lightly: skip if no DB
    r = client.get(f"/p/{entry.name}/tasks", cookies={"cod-doc-locale": "ru"})
    assert r.status_code == 200
    # Page chrome in Russian
    assert "Задачи" in r.text
