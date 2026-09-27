"""AFT-014 (RFC 27 F15), AFT-019: агент ``cod-doc-scout`` читает через MCP, а не sqlite3.

AFT-019: у агента нет allowlist ``tools:`` — в харнессе с отложенными
MCP-тулами он отрезал их вместе с ToolSearch, и агент молча уходил в CLI.
Агент наследует каталог; запись запрещена ``disallowedTools`` и инструкцией.
Тулы, которые агент называет в теле, сверяются с живым каталогом.

Эталон существования тулов — живой каталог профиля ``standard`` (демон
:8801): свежий FastMCP с модулями тулов из ``cod_doc.mcp.server``, затем
``apply_profile``. Ни проза, ни сам agent-файл эталоном не служат.
"""

from __future__ import annotations

import inspect
import re
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
DISALLOWED_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
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


def _meta() -> dict[str, object]:
    _, front, _ = _text().split("---", 2)
    meta = yaml.safe_load(front)
    assert isinstance(meta, dict)
    return meta


def _body() -> str:
    return _text().split("---", 2)[2]


def _named_mcp_tools() -> set[str]:
    return set(re.findall(r"mcp__cod-doc__[a-z_]+", _body()))


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


def test_scout_has_no_tools_allowlist() -> None:
    """AFT-019: allowlist отрезал отложенные MCP-тулы и ToolSearch."""
    assert "tools" not in _meta()


def test_scout_disallows_file_writes() -> None:
    raw = str(_meta().get("disallowedTools", ""))
    disallowed = {t.strip() for t in raw.split(",") if t.strip()}
    assert disallowed >= DISALLOWED_WRITE_TOOLS, sorted(DISALLOWED_WRITE_TOOLS - disallowed)


def test_scout_names_the_read_mcp_tools() -> None:
    named = _named_mcp_tools()
    assert named >= REQUIRED_MCP_TOOLS, sorted(REQUIRED_MCP_TOOLS - named)


def test_scout_explains_loading_deferred_tools() -> None:
    assert "ToolSearch" in _body()


def test_scout_mcp_tools_exist_in_live_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _live_standard_catalog(monkeypatch)
    names = [t.removeprefix(MCP_PREFIX) for t in _named_mcp_tools()]
    assert names
    missing = [n for n in names if n not in catalog]
    assert not missing, f"нет в каталоге профиля standard: {missing}"


def test_scout_tools_are_read_only() -> None:
    offending = [
        t
        for t in _named_mcp_tools()
        if any(t.removeprefix(MCP_PREFIX).startswith(m) for m in MUTATING)
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
