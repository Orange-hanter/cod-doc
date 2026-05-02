"""Изоляция COD-DOC state для api-тестов.

Гарантирует:
- Записи о проектах не утекают в `~/.cod-doc/config.yaml` (изоляция
  через monkeypatch `CONFIG_DIR` / `CONFIG_FILE`).
- Каждый тест стартует с пустым `cod_doc.api.deps._ENGINE_CACHE`
  (после WEB-005 кэш живёт на уровне модуля и иначе утечёт между
  тестами engine-ссылками на удалённые `tmp_path` директории).

⚠ Lifespan vs. set_config footgun:
The FastAPI app lifespan calls `Config.load()` and OVERWRITES whatever
the test pre-set via `deps.set_config(cfg)`. Tests that need a specific
config visible to handlers must call `cfg.save()` BEFORE entering the
TestClient (since CONFIG_FILE is monkeypatched into tmp_path, this is
local). Then access the live config via `deps.get_config()` after entry.
See `tests/api/test_web_settings.py:settings_client` for the pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_cod_doc_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "cod-doc-home"
    home.mkdir()
    monkeypatch.setattr("cod_doc.config.CONFIG_DIR", home)
    monkeypatch.setattr("cod_doc.config.CONFIG_FILE", home / "config.yaml")
    yield home


@pytest.fixture(autouse=True)
def _isolated_engine_cache():
    """Reset the per-project DB engine cache between API tests.

    Module-level state in `cod_doc.api.deps` persists across tests inside
    the same pytest process. Without this fixture, tests that don't
    actively dispose leak Engine handles to deleted tmp_path directories
    — cumulative FD/memory pressure in long suites and harder-to-debug
    test interactions if a path is ever reused.
    """
    from cod_doc.api.deps import dispose_all_engines

    dispose_all_engines()
    yield
    dispose_all_engines()
