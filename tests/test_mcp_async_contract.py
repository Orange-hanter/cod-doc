"""ADO-066: контракт «тул обязан работать под живым сервером».

Дефект, который стерегут эти тесты: `capabilities`, `tool_search` и `tools_diff`
звали ``asyncio.run(mcp.list_tools())`` в теле синхронного тула. Под MCP-сервером
тул исполняется в работающем event loop, поэтому каждый реальный вызов падал с
``RuntimeError: asyncio.run() cannot be called from a running event loop``.

Почему 1630 тестов молчали: старые тесты доставали функцию как
``mcp._tool_manager._tools[name].fn`` и звали её синхронно. Без работающего loop
``asyncio.run()`` совершенно легален — тестовый путь физически не мог
воспроизвести боевой. `capabilities` при этом первая команда, которой агент
осматривает незнакомый проект.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cod_doc.mcp.server import mcp

MCP_DIR = Path(__file__).resolve().parents[1] / "cod_doc" / "mcp"


def _asyncio_run_calls(tree: ast.AST) -> list[int]:
    """Номера строк вызовов ``asyncio.run(...)``."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "run"
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "asyncio"
        ):
            hits.append(node.lineno)
    return hits


def test_no_asyncio_run_anywhere_under_mcp() -> None:
    """Ни один модуль ``cod_doc/mcp/**`` не имеет права звать asyncio.run.

    Всё под mcp/ исполняется внутри работающего loop сервера, поэтому
    ``asyncio.run`` там — гарантированный RuntimeError на первом реальном вызове.
    Нужен список тулов — ``await mcp.list_tools()`` из async-тула.
    """
    offenders: list[str] = []
    for path in sorted(MCP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno in _asyncio_run_calls(tree):
            offenders.append(f"{path.relative_to(MCP_DIR.parents[1])}:{lineno}")
    assert offenders == [], (
        "asyncio.run() под cod_doc/mcp/ — тул упадёт под живым сервером (ADO-066): "
        + ", ".join(offenders)
    )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("capabilities", {}),
        ("tool_search", {"query": "create task", "limit": 1}),
        ("tools_diff", {"since": "this-snapshot-does-not-exist-9999"}),
    ],
)
async def test_discovery_tools_survive_the_real_server_path(
    name: str, arguments: dict[str, object]
) -> None:
    """Вызов через ``mcp.call_tool`` — тот же путь, которым ходит агент.

    До ADO-066 каждый из трёх падал здесь RuntimeError'ом, хотя собственные
    юнит-тесты были зелёные.
    """
    result = await mcp.call_tool(name, arguments)
    assert result is not None
