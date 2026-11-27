"""RFC 39, фаза 0 (REL-001/REL-002): схемы и ошибки link-тулов MCP.

Триггер — вызов агента ``story_link(to_kind="story", relation="see_also")``:
параметры были голыми ``str``, допустимые значения жили только в docstring,
а ошибка ``'story' is not a valid StoryLinkKind`` называла один параметр из
двух невалидных и не перечисляла допустимых значений.

Тулы регистрируются на свежем FastMCP: схема и валидация аргументов — свойство
каталога, БД для них не нужна (валидация падает до ``session_factory``).
"""

from __future__ import annotations

import typing
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from cod_doc.domain.entities import (
    QuestionLinkKind,
    QuestionRelation,
    ScenarioLinkKind,
    ScenarioRelation,
    StoryLinkKind,
    StoryRelation,
)
from cod_doc.infra.models.adrs import ADR_RELATION_KINDS
from cod_doc.mcp.tools import adr_tools, question_tools, scenario_tools, story_tools
from cod_doc.services import adr_service

if TYPE_CHECKING:
    from enum import StrEnum

_ADR_TASK_RELATIONS: frozenset[str] = frozenset(adr_service._LEGAL_RELATIONS)

# (тул, параметр, допустимые значения). Источник значений — домен и
# CHECK-ограничения, а не копия в тесте.
_CASES: list[tuple[str, str, frozenset[str]]] = [
    ("story_link", "to_kind", frozenset(StoryLinkKind)),
    ("story_link", "relation", frozenset(StoryRelation)),
    ("scenario_link", "to_kind", frozenset(ScenarioLinkKind)),
    ("scenario_link", "relation", frozenset(ScenarioRelation)),
    ("question_link", "to_kind", frozenset(QuestionLinkKind)),
    ("question_link", "relation", frozenset(QuestionRelation)),
    ("adr_relate", "kind", frozenset(ADR_RELATION_KINDS)),
    ("adr_unrelate", "kind", frozenset(ADR_RELATION_KINDS)),
    ("adr_link_task", "relation", _ADR_TASK_RELATIONS),
]


@pytest.fixture(scope="module")
def server() -> FastMCP:
    mcp = FastMCP("link-tool-schemas")
    for module in (story_tools, scenario_tools, question_tools, adr_tools):
        module.register(mcp)
    return mcp


async def _schema(server: FastMCP, tool: str) -> dict[str, Any]:
    by_name = {t.name: t for t in await server.list_tools()}
    return by_name[tool].inputSchema


def _enum_of(schema: dict[str, Any], param: str) -> frozenset[str]:
    prop = schema["properties"][param]
    if "$ref" in prop:
        prop = schema["$defs"][prop["$ref"].rsplit("/", 1)[-1]]
    assert "enum" in prop, f"{param}: в схеме нет enum — {prop}"
    return frozenset(prop["enum"])


@pytest.mark.parametrize(("tool", "param", "expected"), _CASES)
async def test_link_tool_schema_lists_allowed_values(
    server: FastMCP, tool: str, param: str, expected: frozenset[str]
) -> None:
    assert _enum_of(await _schema(server, tool), param) == expected


def test_adr_literals_match_storage() -> None:
    """Literal'ы ADR — копия CHECK-ограничений; расхождение ловится здесь."""
    assert frozenset(typing.get_args(adr_tools.ADRRelationKind)) == frozenset(ADR_RELATION_KINDS)
    assert frozenset(typing.get_args(adr_tools.ADRTaskRelation)) == _ADR_TASK_RELATIONS


async def test_story_link_error_names_every_invalid_param(server: FastMCP) -> None:
    """Вызов из триггера: обе ошибки разом и допустимые значения каждой."""
    with pytest.raises(ToolError) as excinfo:
        await server.call_tool(
            "story_link",
            {
                "project": "orakul",
                "story_id": "US-064",
                "to_kind": "story",
                "to_ref": "US-007",
                "relation": "see_also",
            },
        )
    message = str(excinfo.value)
    for param, enum in (("to_kind", StoryLinkKind), ("relation", StoryRelation)):
        assert param in message
        missing = [v for v in _values(enum) if f"'{v}'" not in message]
        assert not missing, f"{param}: в ошибке нет {missing}\n{message}"


def _values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]
