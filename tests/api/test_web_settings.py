"""WEB-060: GET / POST /settings — global config view + form save."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from fastapi.testclient import TestClient

from cod_doc.config import Config

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def settings_client():
    """Persist a Config to disk so the lifespan loads it on TestClient entry.

    The app lifespan calls Config.load() — pre-setting deps._config in the
    fixture is clobbered. Save first, then let lifespan reload, then access
    the live config via deps.get_config().
    """
    initial = Config(
        api_key="sk-or-secret-12345",
        model="anthropic/claude-3-haiku",
        base_url="https://openrouter.ai/api/v1",
        max_tokens=8192,
        max_iterations=50,
        agent_interval=60,
        embedding_model="openai/text-embedding-ada-002",
        auto_commit=False,
    )
    initial.save()

    import cod_doc.api.deps as deps
    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        # `deps.get_config()` returns the lifespan-reloaded instance.
        yield client, deps.get_config()


# ── GET /settings ────────────────────────────────────────────────────────


def test_settings_get_renders_form_with_current_values(settings_client) -> None:
    client, _ = settings_client
    r = client.get("/settings")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # Existing values shown in form fields (non-secret)
    assert 'value="anthropic/claude-3-haiku"' in r.text
    assert 'value="https://openrouter.ai/api/v1"' in r.text
    assert 'value="8192"' in r.text
    # API key NOT displayed in plaintext
    assert "sk-or-secret-12345" not in r.text


def test_settings_get_masks_api_key(settings_client) -> None:
    client, _ = settings_client
    r = client.get("/settings")
    # Mask shows last 4 chars only.
    assert "2345" in r.text
    assert "…2345" in r.text


def test_settings_get_handles_unset_api_key() -> None:
    Config().save()  # persist defaults (no api_key)
    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/settings")
    assert r.status_code == 200
    assert "Ключ не установлен" in r.text


# ── POST /settings ───────────────────────────────────────────────────────


def test_settings_post_saves_and_redirects(settings_client) -> None:
    client, cfg = settings_client
    r = client.post(
        "/settings",
        data={
            "api_key": "",  # empty → keep existing
            "base_url": "https://api.example.com/v1",
            "model": "test/model",
            "max_tokens": "4096",
            "auto_commit": "on",
            "max_iterations": "30",
            "agent_interval": "120",
            "embedding_model": "openai/text-embedding-3-small",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/settings"
    # In-memory config updated
    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "test/model"
    assert cfg.max_tokens == 4096
    assert cfg.auto_commit is True
    assert cfg.max_iterations == 30
    assert cfg.agent_interval == 120
    assert cfg.embedding_model == "openai/text-embedding-3-small"
    # API key NOT touched (empty form value → keep existing)
    assert cfg.api_key == "sk-or-secret-12345"


def test_settings_post_persists_to_config_file(settings_client, tmp_path: Path) -> None:
    """Config.save() writes to CONFIG_FILE (isolated by autouse fixture)."""
    client, _ = settings_client
    client.post(
        "/settings",
        data={
            "api_key": "",
            "base_url": "https://api.example.com/v1",
            "model": "test/persisted",
            "max_tokens": "1024",
            "auto_commit": "",
            "max_iterations": "10",
            "agent_interval": "30",
            "embedding_model": "openai/text-embedding-3-small",
        },
        follow_redirects=False,
    )
    # The autouse fixture in conftest.py points CONFIG_FILE at tmp_path / cod-doc-home / config.yaml
    home = tmp_path / "cod-doc-home"
    config_file = home / "config.yaml"
    assert config_file.exists()
    data = yaml.safe_load(config_file.read_text())
    assert data["model"] == "test/persisted"
    assert data["max_tokens"] == 1024
    assert data["auto_commit"] is False


def test_settings_post_explicit_dash_clears_api_key(settings_client) -> None:
    client, cfg = settings_client
    client.post(
        "/settings",
        data={
            "api_key": "-",
            "base_url": cfg.base_url,
            "model": cfg.model,
            "max_tokens": str(cfg.max_tokens),
            "auto_commit": "" if not cfg.auto_commit else "on",
            "max_iterations": str(cfg.max_iterations),
            "agent_interval": str(cfg.agent_interval),
            "embedding_model": cfg.embedding_model,
        },
        follow_redirects=False,
    )
    assert cfg.api_key == ""


def test_settings_post_new_api_key_replaces(settings_client) -> None:
    client, cfg = settings_client
    client.post(
        "/settings",
        data={
            "api_key": "sk-newer-secret-9876",
            "base_url": cfg.base_url,
            "model": cfg.model,
            "max_tokens": str(cfg.max_tokens),
            "auto_commit": "" if not cfg.auto_commit else "on",
            "max_iterations": str(cfg.max_iterations),
            "agent_interval": str(cfg.agent_interval),
            "embedding_model": cfg.embedding_model,
        },
        follow_redirects=False,
    )
    assert cfg.api_key == "sk-newer-secret-9876"


def test_settings_post_uncheck_auto_commit_clears_it(settings_client) -> None:
    """Checkbox absent in form → auto_commit becomes False."""
    client, cfg = settings_client
    cfg.auto_commit = True

    client.post(
        "/settings",
        data={
            "api_key": "",
            "base_url": cfg.base_url,
            "model": cfg.model,
            "max_tokens": str(cfg.max_tokens),
            # no auto_commit field
            "max_iterations": str(cfg.max_iterations),
            "agent_interval": str(cfg.agent_interval),
            "embedding_model": cfg.embedding_model,
        },
        follow_redirects=False,
    )
    assert cfg.auto_commit is False
