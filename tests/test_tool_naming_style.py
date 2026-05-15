"""PCA-939: anti-drift — все ``@mcp.tool(name="...")`` имена в snake_case.

История: до PCA-939 в исходниках смешивались ``name="doc.list"`` (47 шт.)
и ``name="task_create"`` (22 шт.). FastMCP сглаживает точки в подчёркивания
на транспорте, но grep'абельность внутри репо страдала: «найти все plan.*»
требовало одну команду, «все task_*» — другую.

Этот тест ловит регрессию — если кто-то добавит новый тул со старым
dotted-стилем, прогон CI скажет об этом.
"""

from __future__ import annotations

import re
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "cod_doc" / "mcp" / "tools"

_DOTTED_NAME_RE = re.compile(
    r'@mcp\.tool\(name="([a-z_]+\.[a-z_]+)"\)',
)


def test_no_dotted_tool_names_in_source() -> None:
    offenders: list[tuple[Path, str]] = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _DOTTED_NAME_RE.finditer(text):
            offenders.append((path, match.group(1)))

    assert not offenders, (
        "PCA-939: every @mcp.tool(name=...) must use snake_case "
        "(no dots). Offending registrations:\n"
        + "\n".join(f"  {p.name}: {n!r}" for p, n in offenders)
    )
