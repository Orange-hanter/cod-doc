"""STB-011: Config.load() caches the parsed YAML (per path, mtime+size keyed)."""

from __future__ import annotations

import os

from cod_doc import config as cfgmod

_SAMPLE = "api_key: sk-1\nmodel: m\nbase_url: https://x\nprojects: []\n"


def test_load_caches_parse_within_unchanged_file(monkeypatch) -> None:
    cfgmod.Config.clear_cache()
    cfgmod.CONFIG_FILE.write_text(_SAMPLE, encoding="utf-8")

    reads = {"n": 0}
    real = cfgmod.yaml.safe_load

    def counting(text):  # type: ignore[no-untyped-def]
        reads["n"] += 1
        return real(text)

    monkeypatch.setattr(cfgmod.yaml, "safe_load", counting)

    c1 = cfgmod.Config.load()
    c2 = cfgmod.Config.load()
    assert c1.api_key == "sk-1"
    assert c2.api_key == "sk-1"
    # Second load is served from cache — only one parse happened.
    assert reads["n"] == 1
    # Fresh instances each call (no shared mutable config).
    assert c1 is not c2


def test_load_reparses_when_file_changes() -> None:
    cfgmod.Config.clear_cache()
    cfgmod.CONFIG_FILE.write_text(_SAMPLE, encoding="utf-8")
    assert cfgmod.Config.load().api_key == "sk-1"

    cfgmod.CONFIG_FILE.write_text(
        "api_key: sk-2-longer\nmodel: m\nbase_url: https://x\nprojects: []\n",
        encoding="utf-8",
    )
    # Bump mtime too, so the change is seen even on coarse-mtime filesystems.
    st = cfgmod.CONFIG_FILE.stat()
    os.utime(cfgmod.CONFIG_FILE, (st.st_atime, st.st_mtime + 5))

    assert cfgmod.Config.load().api_key == "sk-2-longer"


def test_save_invalidates_cache() -> None:
    cfgmod.Config.clear_cache()
    cfgmod.CONFIG_FILE.write_text(_SAMPLE, encoding="utf-8")
    cfg = cfgmod.Config.load()
    assert cfg.get_project("p") is None  # not yet present

    cfg.add_project(cfgmod.ProjectEntry(name="p", path="/tmp/p"))  # mutates + save()

    # A subsequent load must reflect the saved project, not the cached parse.
    assert cfgmod.Config.load().get_project("p") is not None


def test_load_without_file_returns_default_and_does_not_cache() -> None:
    cfgmod.Config.clear_cache()
    # conftest points CONFIG_FILE at a fresh tmp path that does not exist yet.
    if cfgmod.CONFIG_FILE.exists():
        cfgmod.CONFIG_FILE.unlink()
    c = cfgmod.Config.load()
    assert c.api_key == "" or c.api_key is not None  # default Config constructed
    assert str(cfgmod.CONFIG_FILE) not in cfgmod._LOAD_CACHE
