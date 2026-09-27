"""RFC 27 F15 / AFT-014: инструкции агента не читают ``state.db`` напрямую.

Прямой ``sqlite3`` по ``.cod-doc/state.db`` был штатным путём в самих
скиллах и командах плагина — замер насчитал 407 таких чтений за 40 сессий.
Слаг проекта берётся через ``cod-doc project list --json``, задачи плана —
через ``task_list``. Исключение — скрипты хуков в ``plugins/cod-doc/scripts/``,
где sqlite3 остаётся с обоснованием бюджета; этот гейт их не сканирует.

Тулы, названные в ``ground-truth-reconcile``, сверяются с живым каталогом
профиля ``full``: сверка с прозой поймала бы только расхождение двух копий.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.mcp import server as mcp_server

ROOT = Path(__file__).resolve().parents[1]

INSTRUCTION_DIRS = (
    "plugins/cod-doc/skills",
    "plugins/cod-doc/commands",
    "plugins/cod-doc/agents",
    "cod_doc/skills",
)

SLUG_VIA_CLI_FILES = (
    "plugins/cod-doc/skills/doc-sync/SKILL.md",
    "plugins/cod-doc/commands/status.md",
    "plugins/cod-doc/commands/setup.md",
)

GROUND_TRUTH = ROOT / "cod_doc/skills/ground-truth-reconcile/SKILL.md"
GROUND_TRUTH_TOOLS = ("task_list", "plan_list")


def test_no_sqlite_in_instructions() -> None:
    files = [p for d in INSTRUCTION_DIRS for p in sorted((ROOT / d).rglob("*.md"))]
    # Пустой glob (переименовали каталог) молча выключил бы проверку.
    assert len(files) >= 10, files
    offenders = [
        str(p.relative_to(ROOT)) for p in files if "sqlite3" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.parametrize("path", SLUG_VIA_CLI_FILES)
def test_slug_via_cli(path: str) -> None:
    assert "cod-doc project list --json" in (ROOT / path).read_text(encoding="utf-8")


def test_ground_truth_uses_task_list() -> None:
    text = GROUND_TRUTH.read_text(encoding="utf-8")
    assert "task_list(" in text
    assert "plan_scope" in text
    assert "state.db" not in text
    assert "SELECT" not in text


def test_named_tools_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    # Свежий FastMCP: модульный mcp_server.mcp могли обрезать другие тесты процесса.
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
    mcp_server.apply_profile("full")
    catalog = set(fresh._tool_manager._tools)

    text = GROUND_TRUTH.read_text(encoding="utf-8")
    for tool in GROUND_TRUTH_TOOLS:
        assert f"{tool}(" in text, tool
        assert tool in catalog, tool
