"""REL-013: скилл `relations` называет только существующие MCP-тулы."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from cod_doc.mcp.server import mcp

SKILL = Path(__file__).resolve().parents[1] / "cod_doc" / "skills" / "relations" / "SKILL.md"
_TOOLISH = re.compile(r"`((?:relate|unrelate|relations|relation_\w+|(?:task|adr)_\w+))(?:\(|`)")


def test_relations_skill_names_existing_tools() -> None:
    named = set(_TOOLISH.findall(SKILL.read_text(encoding="utf-8")))
    available = {t.name for t in asyncio.run(mcp.list_tools())}
    # Анти-tautology: скилл обязан называть тулы записи и обходные тулы бэкендов.
    assert {"relate", "relations", "relation_types", "task_add_dependency", "adr_relate"} <= named
    assert not named - available, sorted(named - available)
