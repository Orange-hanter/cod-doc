"""PCA-933: docstring task-status MCP-тулов и skill `task-standard`
должны перечислять все 7 канонических TaskStatus.

Single source of truth — `cod_doc.services.task_status_machine.ALLOWED_TRANSITIONS`.
Если кто-то добавит / переименует bucket в state-machine, эти тесты
ловят drift в docstring'ах и скиле, которые могут «застрять» в старой
3-state или 7-state, но без свежего bucket.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cod_doc.mcp.server import mcp
from cod_doc.services.task_status_machine import ALLOWED_TRANSITIONS

CANONICAL_STATUSES: frozenset[str] = frozenset(
    set(ALLOWED_TRANSITIONS.keys()) | {dst for dsts in ALLOWED_TRANSITIONS.values() for dst in dsts}
)

# Тулы, которые принимают status как enum (list / direct update) и должны
# документировать весь набор. task_checkout — single-transition tool, для
# него отдельный тест ниже: мы ожидаем только ссылку на SoT-файл.
DOC_REQUIRED_TOOLS = ("task_update_status", "task_list")


def _tool_descriptions() -> dict[str, str]:
    tools = asyncio.run(mcp.list_tools())
    return {t.name: (t.description or "") for t in tools}


@pytest.mark.parametrize("tool_name", DOC_REQUIRED_TOOLS)
def test_status_aware_tools_mention_all_canonical_statuses(tool_name: str) -> None:
    descriptions = _tool_descriptions()
    assert tool_name in descriptions, f"Expected tool {tool_name!r} to be registered on MCP server."
    desc = descriptions[tool_name]
    missing = sorted(s for s in CANONICAL_STATUSES if s not in desc)
    assert not missing, (
        f"{tool_name} docstring is missing TaskStatus value(s): {missing}. "
        f"Canonical buckets from task_status_machine.ALLOWED_TRANSITIONS "
        f"= {sorted(CANONICAL_STATUSES)}."
    )


def test_task_standard_skill_lists_all_canonical_statuses() -> None:
    skill = (
        Path(__file__).resolve().parents[1] / "cod_doc" / "skills" / "task-standard" / "SKILL.md"
    )
    text = skill.read_text(encoding="utf-8")
    missing = sorted(s for s in CANONICAL_STATUSES if s not in text)
    assert not missing, (
        f"skill task-standard/SKILL.md does not mention status(es): {missing}. "
        f"Canonical buckets: {sorted(CANONICAL_STATUSES)}."
    )


def test_task_checkout_docstring_links_to_state_machine_source() -> None:
    """``task_checkout`` управляет только одной транзицией (todo → in_progress),
    но обязан указать читателю, где смотреть полный state graph и алиасы.
    """
    desc = _tool_descriptions().get("task_checkout", "")
    assert "task_status_machine" in desc, (
        "task_checkout docstring должен ссылаться на "
        "cod_doc/services/task_status_machine.py — единый источник правды "
        "для TaskStatus переходов и legacy-алиасов."
    )


def test_canonical_set_is_seven_states() -> None:
    """Anti-tautology: убеждаемся, что CANONICAL_STATUSES — это 7 значений.

    Если proposal 08 / state-machine изменится — тест явно покажет, что
    тесты выше теперь оперируют другим количеством buckets.
    """
    assert len(CANONICAL_STATUSES) == 7, (
        f"Expected 7-state TaskStatus taxonomy, got {len(CANONICAL_STATUSES)}: "
        f"{sorted(CANONICAL_STATUSES)}"
    )
