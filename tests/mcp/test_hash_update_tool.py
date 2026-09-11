"""MCP ``hash_update`` rewrites hybrid-ref sha values in MASTER.md."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from cod_doc.core.hash_calc import calc_hash
from cod_doc.mcp.tools import hash_tools

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def test_hash_update_rewrites_stale_sha(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    spec = tmp_path / "specs" / "auth.md"
    spec.parent.mkdir()
    spec.write_text("# Auth spec\n", encoding="utf-8")
    master = tmp_path / "MASTER.md"
    master.write_text(
        "📁 /specs/auth.md | 🗃️ doc:specs_auth_md | 🔑 sha:aaaaaaaaaaaa\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        hash_tools,
        "session_factory",
        lambda project: (None, SimpleNamespace(path=str(tmp_path))),
    )
    mcp = FastMCP("test")
    hash_tools.register(mcp)
    out = _get_tool(mcp, "hash_update")(project="p")

    assert out["updated"] >= 1
    assert calc_hash(spec) in master.read_text(encoding="utf-8")
    assert out["path"] == str(master.resolve())
