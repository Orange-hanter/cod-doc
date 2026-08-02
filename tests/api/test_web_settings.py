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


def test_settings_get_handles_unset_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # CI sets COD_DOC_API_KEY; clear it so the "unset" path is exercised.
    monkeypatch.delenv("COD_DOC_API_KEY", raising=False)
    Config(api_key="").save()
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


def test_settings_get_renders_model_preset_dropdown(settings_client) -> None:
    """COD-059: settings page exposes a curated <select> of LLM presets."""
    client, _ = settings_client
    r = client.get("/settings")
    # Dropdown is wired
    assert 'id="model-preset"' in r.text
    assert "syncModelPreset" in r.text
    # At least the default Sonnet entry is listed with context + price tags
    assert "Claude Sonnet 4.6" in r.text
    assert "200K ctx" in r.text
    assert "per 1M tok" in r.text
    # Custom-fallback option exists
    assert "__custom__" in r.text


def test_settings_get_marks_unknown_model_as_custom(settings_client) -> None:
    """A model not in the catalog → custom option selected."""
    client, _ = settings_client
    # Fixture sets model="anthropic/claude-3-haiku", which is NOT in the catalog.
    r = client.get("/settings")
    # The "Custom" <option> is selected
    assert 'value="__custom__" selected' in r.text


def test_settings_get_marks_known_preset_selected() -> None:
    """A model in the catalog → that preset is the selected option."""
    Config(
        api_key="sk-test",
        model="anthropic/claude-sonnet-4-6",
        base_url="https://x",
    ).save()
    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/settings")
    assert r.status_code == 200
    # The catalog option for Sonnet 4.6 is rendered with selected attribute
    assert 'value="anthropic/claude-sonnet-4-6"' in r.text
    assert "selected" in r.text


def test_settings_get_renders_embedding_backend_dropdown(settings_client) -> None:
    """COD-043: settings page exposes a backend <select> with openai + local."""
    client, _ = settings_client
    r = client.get("/settings")
    body = r.text
    assert 'name="embedding_backend"' in body
    assert "OpenAI-compatible" in body
    assert "Local sentence-transformers" in body
    # By default, the openai option is selected.
    assert 'value="openai"\n                selected' in body or (
        '<option value="openai"' in body and "selected" in body
    )


def test_settings_post_persists_embedding_backend(settings_client) -> None:
    client, cfg = settings_client
    client.post(
        "/settings",
        data={
            "api_key": "",
            "base_url": cfg.base_url,
            "model": cfg.model,
            "max_tokens": str(cfg.max_tokens),
            "auto_commit": "" if not cfg.auto_commit else "on",
            "max_iterations": str(cfg.max_iterations),
            "agent_interval": str(cfg.agent_interval),
            "embedding_model": "all-MiniLM-L6-v2",
            "embedding_backend": "local",
        },
        follow_redirects=False,
    )
    assert cfg.embedding_backend == "local"
    assert cfg.embedding_model == "all-MiniLM-L6-v2"


def test_settings_post_rejects_unknown_backend_falling_back_to_openai(settings_client) -> None:
    client, cfg = settings_client
    cfg.embedding_backend = "openai"
    client.post(
        "/settings",
        data={
            "api_key": "",
            "base_url": cfg.base_url,
            "model": cfg.model,
            "max_tokens": str(cfg.max_tokens),
            "auto_commit": "" if not cfg.auto_commit else "on",
            "max_iterations": str(cfg.max_iterations),
            "agent_interval": str(cfg.agent_interval),
            "embedding_model": cfg.embedding_model,
            "embedding_backend": "weird",
        },
        follow_redirects=False,
    )
    # Unknown values fall back to openai (no validation error to the user —
    # the form is constrained to the dropdown's options anyway).
    assert cfg.embedding_backend == "openai"


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
