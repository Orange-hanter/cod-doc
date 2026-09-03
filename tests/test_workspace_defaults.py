"""PCA-945: set_default_project / get_default_project / clear_default_project."""

from __future__ import annotations

from typing import Any

import pytest

from cod_doc.mcp.server import mcp as live_mcp
from cod_doc.mcp.tools import _workspace


def _get_tool(name: str) -> Any:
    return live_mcp._tool_manager._tools[name].fn


def setup_function() -> None:
    _workspace.clear()


def teardown_function() -> None:
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


def test_resolve_helper_raises_without_default() -> None:
    with pytest.raises(ValueError, match="No `project`"):
        _workspace.resolve(None)


def test_resolve_helper_returns_explicit_over_default() -> None:
    _workspace.set_("default-x")
    assert _workspace.resolve("explicit-y") == "explicit-y"
    assert _workspace.resolve(None) == "default-x"
