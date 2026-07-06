"""PCA-931: smoke-проверка, что orchestrator SKILL.md и references не
ссылаются на MCP-инструменты, которых нет в реальной поверхности.

Логика:

1. Извлекает из ``cod_doc/skills/orchestrator/SKILL.md`` и
   ``references/self-check.md`` все backtick-обёрнутые идентификаторы
   формы ``foo_bar(``  — это «вызовы тулов» в прозе скилла.
2. Для каждого имени проверяет, что оно либо присутствует в
   ``mcp.list_tools()`` (с учётом dot ↔ underscore aliasing — FastMCP
   нормализует точки в подчёркивания на транспорте), либо явно
   присутствует в ``NON_MCP_ALLOWED`` (не-MCP примитивы: статусные
   имена, CLI-команды, ссылки на скиллы).

Падает, если orchestrator-скилл учит агента вызывать тул, которого нет
в реальном каталоге — как было до PCA-931, когда SKILL ссылался на
``get_context`` / ``write_file`` / ``calc_hash`` / ``git_commit`` /
``ask_human``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from cod_doc.mcp.server import mcp

SKILL_ROOT = Path(__file__).resolve().parents[1] / "cod_doc" / "skills" / "orchestrator"
SKILL_FILES = (SKILL_ROOT / "SKILL.md", SKILL_ROOT / "references" / "self-check.md")

# Backtick-wrapped call expression: `foo_bar(` or `foo.bar(` или просто
# `module.tool(...)`. Захватывает имя тула до открывающей скобки.
_TOOL_CALL_RE = re.compile(r"`([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*)\s*\(")

# Имена, которые упоминаются в backtick'ах как «вызовы», но это
# не MCP-тулы — это скилы, статусные значения, ссылки на skill-имена и т.п.
NON_MCP_ALLOWED: frozenset[str] = frozenset(
    {
        # Это skill-имена (упоминаются как «см. скилл `name`»).
        "task-standard",
        "plan-to-tasks",
        "drift-handling",
        "validation",
        "module-audit",
        "audit-cadence",
        # Cycle-5: SKILL.md обращается к 4 admin-tier тулам названиями
        # как пример «избыточно для agent flow». Они существуют, но в
        # agent профиле не доступны.
        "skill_get",
        "task_create",
        "doc_body",
        "plan_ready",
    }
)


def _mcp_tool_names() -> set[str]:
    tools = asyncio.run(mcp.list_tools())
    names: set[str] = set()
    for tool in tools:
        names.add(tool.name)
        # FastMCP / MCP clients иногда показывают точечные имена как
        # snake_case (Claude Code mangling) — учитываем оба варианта.
        if "." in tool.name:
            names.add(tool.name.replace(".", "_"))
        if "_" in tool.name:
            # Иначе test_mcp.py:test_mcp_lists_tools уже фиксирует имя как dotted —
            # но мы добавим snake-вариант на всякий случай.
            names.add(tool.name)
    return names


def _extract_tool_refs(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(_TOOL_CALL_RE.findall(text))


@pytest.mark.parametrize("skill_path", SKILL_FILES, ids=[p.name for p in SKILL_FILES])
def test_orchestrator_skill_tool_refs_resolve(skill_path: Path) -> None:
    referenced = _extract_tool_refs(skill_path)
    available = _mcp_tool_names()

    missing = {name for name in referenced if name not in available and name not in NON_MCP_ALLOWED}
    assert not missing, (
        f"{skill_path.name} references tool(s) not in MCP catalog "
        f"and not in NON_MCP_ALLOWED: {sorted(missing)}. "
        "Либо добавь тул в cod_doc/mcp/tools/, либо в NON_MCP_ALLOWED "
        "(если это skill-name / CLI / config-key)."
    )


def test_orchestrator_skill_actually_references_some_mcp_tools() -> None:
    """Анти-tautology: убеждаемся, что в SKILL.md ВООБЩЕ есть tool-refs.

    Если регэксп вдруг перестанет матчить (например после форматных
    изменений) — параметризованный тест выше будет ложно зелёный.
    """
    skill_md = SKILL_ROOT / "SKILL.md"
    refs = _extract_tool_refs(skill_md)
    available = _mcp_tool_names()
    real = {name for name in refs if name in available}
    assert len(real) >= 5, (
        f"SKILL.md должен ссылаться минимум на 5 настоящих MCP-тулов; "
        f"нашёл {len(real)}: {sorted(real)}."
    )
