"""PCA-945: set_default_project / get_default_project / clear_default_project.

The second half covers shared (streamable-http) mode, where the
process-wide default is disabled outright — see
``cod_doc/mcp/tools/_workspace.py`` for why.
"""

from __future__ import annotations

from typing import Any

import pytest

from cod_doc.mcp import server as mcp_server
from cod_doc.mcp.server import mcp as live_mcp
from cod_doc.mcp.tools import _workspace


def _get_tool(name: str) -> Any:
    return live_mcp._tool_manager._tools[name].fn


def setup_function() -> None:
    _workspace.set_shared(False)
    _workspace.clear()


def teardown_function() -> None:
    # Shared mode is process-wide: leaking it True would break every later
    # test that relies on the default project.
    _workspace.set_shared(False)
    _workspace.clear()


def test_default_is_null_initially() -> None:
    get = _get_tool("get_default_project")
    assert get()["default_project"] is None


def test_set_and_get_default_project() -> None:
    set_ = _get_tool("set_default_project")
    get = _get_tool("get_default_project")
    set_(name="my-proj")
    assert get()["default_project"] == "my-proj"


def test_clear_default_project() -> None:
    set_ = _get_tool("set_default_project")
    clear = _get_tool("clear_default_project")
    get = _get_tool("get_default_project")
    set_(name="my-proj")
    assert get()["default_project"] == "my-proj"
    clear()
    assert get()["default_project"] is None


async def test_capabilities_reflects_default_project() -> None:
    set_ = _get_tool("set_default_project")
    capabilities = _get_tool("capabilities")
    set_(name="caps-proj")
    result = await capabilities()
    assert result["session"]["default_project"] == "caps-proj"
    assert result["session"]["shared_server"] is False


def test_resolve_helper_raises_without_default() -> None:
    with pytest.raises(ValueError, match="No `project`"):
        _workspace.resolve(None)


def test_resolve_helper_returns_explicit_over_default() -> None:
    _workspace.set_("default-x")
    assert _workspace.resolve("explicit-y") == "explicit-y"
    assert _workspace.resolve(None) == "default-x"


# --------------------------------------------------------------------------- #
# Shared mode — one process fronting every harness over HTTP.                  #
# --------------------------------------------------------------------------- #


def test_shared_mode_requires_explicit_project() -> None:
    _workspace.set_shared(True)
    with pytest.raises(ValueError, match="`project` is required"):
        _workspace.resolve(None)


def test_shared_mode_still_honours_explicit_project() -> None:
    _workspace.set_shared(True)
    assert _workspace.resolve("explicit-y") == "explicit-y"


def test_shared_mode_refuses_to_install_a_default() -> None:
    _workspace.set_shared(True)
    with pytest.raises(ValueError, match="Setting a default project is unavailable"):
        _workspace.set_("sneaky")
    assert _workspace.get() is None


def test_shared_mode_drops_a_default_installed_earlier() -> None:
    _workspace.set_("stdio-era")
    assert _workspace.get() == "stdio-era"
    _workspace.set_shared(True)
    assert _workspace.get() is None


def test_shared_mode_allows_clearing() -> None:
    """``clear()`` is a no-op under shared mode, not an error."""
    _workspace.set_shared(True)
    _workspace.clear()
    assert _workspace.get() is None


def test_set_default_project_tool_raises_in_shared_mode() -> None:
    _workspace.set_shared(True)
    set_ = _get_tool("set_default_project")
    with pytest.raises(ValueError, match="Setting a default project is unavailable"):
        set_(name="my-proj")


async def test_capabilities_flags_shared_server() -> None:
    _workspace.set_shared(True)
    capabilities = _get_tool("capabilities")
    result = await capabilities()
    assert result["session"]["shared_server"] is True
    assert result["session"]["default_project"] is None
    assert "required" in result["session"]["default_project_usage"]


@pytest.fixture
def _restore_profile():
    """``run_mcp_server`` applies a profile; keep that out of other tests.

    Mirrors the autouse fixture in tests/test_server_profiles.py — the tool
    catalog and the active profile are module-level state on the server.
    """
    snapshot = dict(mcp_server.mcp._tool_manager._tools)
    active = mcp_server.get_active_profile()
    yield
    mcp_server.mcp._tool_manager._tools.clear()
    mcp_server.mcp._tool_manager._tools.update(snapshot)
    mcp_server._ACTIVE_PROFILE = active


@pytest.mark.usefixtures("_restore_profile")
def test_stdio_transport_leaves_shared_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server.mcp, "run", lambda *args, **kwargs: None)
    mcp_server.run_mcp_server(transport="stdio", host="127.0.0.1", port=8001, profile="full")
    assert _workspace.is_shared() is False


@pytest.mark.usefixtures("_restore_profile")
def test_http_transport_turns_shared_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server.mcp, "run", lambda *args, **kwargs: None)
    mcp_server.run_mcp_server(
        transport="streamable-http", host="127.0.0.1", port=8801, profile="full"
    )
    assert _workspace.is_shared() is True
