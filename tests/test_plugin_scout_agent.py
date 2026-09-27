"""AFT-014 (RFC 27 F15): агент ``cod-doc-scout`` читает через MCP, а не sqlite3.

Эталон существования тулов — живой каталог профиля ``standard`` (демон
:8801): свежий FastMCP с модулями тулов из ``cod_doc.mcp.server``, затем
``apply_profile``. Ни проза, ни сам agent-файл эталоном не служат.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from mcp.server.fastmcp import FastMCP

from cod_doc.mcp import server as mcp_server

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "plugins" / "cod-doc" / "agents" / "cod-doc-scout.md"

MCP_PREFIX = "mcp__cod-doc__"
REQUIRED_MCP_TOOLS = {
    "mcp__cod-doc__ctx_search",
    "mcp__cod-doc__context_get",
    "mcp__cod-doc__task_get",
    "mcp__cod-doc__task_list",
    "mcp__cod-doc__doc_get",
    "mcp__cod-doc__doc_section_get",
    "mcp__cod-doc__plan_list",
    "mcp__cod-doc__plan_progress",
    "mcp__cod-doc__adr_get",
}
REQUIRED_BUILTIN_TOOLS = {"Bash", "Read", "Grep", "Glob"}
MUTATING = (
    "task_create",
    "task_checkout",
    "task_complete",
    "doc_patch_section",
    "doc_create",
    "plan_create",
    "task_update",
)

ORDER_SECTION_START = "**Инструменты по порядку**"
ORDER_SECTION_END = "**Формат ответа**"
MCP_MARKERS = ("mcp__cod-doc__", "ctx_search", "task_get", "task_list", "plan_progress")
CLI_MARKERS = ("cod-doc task", "cod-doc plan")
READ_MARKER = "**Read/Grep файла проекции**"


def _text() -> str:
    return SCOUT.read_text(encoding="utf-8")


def _tools() -> list[str]:
    _, front, _ = _text().split("---", 2)
    meta = yaml.safe_load(front)
    return [t.strip() for t in meta["tools"].split(",")]


def _live_standard_catalog(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    fresh = FastMCP("COD-DOC", json_response=True)
    for value in vars(mcp_server).values():
        if (
            inspect.ismodule(value)
            and value.__name__.startswith("cod_doc.mcp.tools.")
            and hasattr(value, "register")
        ):
            value.register(fresh)
    monkeypatch.setattr(mcp_server, "mcp", fresh)
    monkeypatch.setattr(mcp_server, "_ACTIVE_PROFILE", mcp_server._ACTIVE_PROFILE)
    monkeypatch.setenv("COD_DOC_ACTIVE_PROFILE", "full")
    mcp_server.apply_profile("standard")
    return set(fresh._tool_manager._tools)


def test_scout_tools_include_mcp_read_tools() -> None:
    tools = set(_tools())
    assert tools >= REQUIRED_MCP_TOOLS | REQUIRED_BUILTIN_TOOLS, (
        f"не хватает: {sorted((REQUIRED_MCP_TOOLS | REQUIRED_BUILTIN_TOOLS) - tools)}"
    )


def test_scout_mcp_tools_exist_in_live_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _live_standard_catalog(monkeypatch)
    names = [t.removeprefix(MCP_PREFIX) for t in _tools() if t.startswith(MCP_PREFIX)]
    assert names
    missing = [n for n in names if n not in catalog]
    assert not missing, f"нет в каталоге профиля standard: {missing}"


def test_scout_tools_are_read_only() -> None:
    offending = [
        t for t in _tools() if any(t.removeprefix(MCP_PREFIX).startswith(m) for m in MUTATING)
    ]
    assert not offending, f"мутирующие тулы у read-only агента: {offending}"


def test_scout_has_no_sqlite() -> None:
    text = _text()
    assert "sqlite3" not in text
    assert "cod-doc project list --json" in text


def _first(section: str, markers: tuple[str, ...]) -> int:
    positions = [section.find(m) for m in markers if m in section]
    assert positions, f"ни одного маркера {markers} в разделе порядка чтения"
    return min(positions)


def test_scout_reading_order_mcp_cli_read() -> None:
    text = _text()
    start = text.index(ORDER_SECTION_START)
    end = text.index(ORDER_SECTION_END, start)
    section = text[start:end]
    mcp_pos = _first(section, MCP_MARKERS)
    cli_pos = _first(section, CLI_MARKERS)
    read_pos = _first(section, (READ_MARKER,))
    assert mcp_pos < cli_pos < read_pos, (mcp_pos, cli_pos, read_pos)
