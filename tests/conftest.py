"""Global test isolation for COD-DOC runtime state.

Keeps tests from reading or writing the user's real ``~/.cod-doc`` registry and
prevents workspace-local project discovery from leaking the current checkout into
tests that intentionally create an empty config.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_cod_doc_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Path:
    home = tmp_path / "cod-doc-home"
    home.mkdir()
    monkeypatch.setenv("COD_DOC_HOME", str(home))
    monkeypatch.setattr("cod_doc.config.CONFIG_DIR", home)
    monkeypatch.setattr("cod_doc.config.CONFIG_FILE", home / "config.yaml")

    # Workspace discovery is a useful runtime fallback, but in tests it makes
    # an empty Config accidentally include the repository under test. Keep the
    # dedicated discovery tests real; isolate everything else.
    if Path(str(request.node.path)).name != "test_config_workspace_discovery.py":
        monkeypatch.setattr("cod_doc.config._discover_workspace_projects", lambda start=None: [])
        monkeypatch.setattr("cod_doc.config._discover_workspace_project", lambda name: None)
    return home


@pytest.fixture(autouse=True)
def isolated_api_runtime_state() -> None:
    """Reset process-wide API state that can leak between TestClient cases."""
    from cod_doc.api.deps import dispose_all_engines, set_config, stop_daemon, webhook_registry
    from cod_doc.config import Config

    stop_daemon()
    dispose_all_engines()
    webhook_registry.clear()
    set_config(Config())
    yield
    stop_daemon()
    dispose_all_engines()
    webhook_registry.clear()
    set_config(Config())
