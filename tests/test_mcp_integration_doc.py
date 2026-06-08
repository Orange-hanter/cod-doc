"""PCA-932: anti-drift — числовые утверждения в docs/mcp-integration.md
обязаны совпадать с реальным каталогом MCP-сервера.

История: документ обещал «23 инструмента» в шести местах, фактически уже
давно экспонировано 93. Любой агент / новый контрибьютор, ориентируясь
на этот документ, строил неверную модель возможностей.

Тест:

1. Читает docs/mcp-integration.md.
2. Извлекает заявленный total ("**N инструментов**" / "**N**" в строке
   ИТОГО / "N тула на текущий релиз" — допускаются три формы).
3. Сравнивает с ``len(await mcp.list_tools())``.
4. Дополнительно проверяет, что счётчики семейств в таблице каталога
   суммируются к total.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from cod_doc.mcp.server import mcp

DOC = Path(__file__).resolve().parents[1] / "docs" / "mcp-integration.md"

# Совпадает с любым из:
#   **93 инструментами**
#   **93 инструмента**
#   **93 тула**
#   93 тула на текущий
_TOTAL_CLAIM_RE = re.compile(r"\*\*?(\d{1,4})\s*(?:инструмент\w*|тул\w*)\*?\*?")

# Строка таблицы каталога: «| **семейство** | 12 | ...»
# Имя семейства может содержать `.\*` (например `doc.\*`), поэтому в
# capture-группе разрешаем любые символы кроме pipe.
_FAMILY_ROW_RE = re.compile(
    r"^\|\s*\*\*(?!ИТОГО)[^|]+?\*\*\s*\|\s*(\d+)\s*\|",
    re.MULTILINE,
)

# Строка с ИТОГО: «| **ИТОГО** | **93** | ... |»
_TOTAL_ROW_RE = re.compile(
    r"\|\s*\*\*ИТОГО\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|",
)


def _real_tool_count() -> int:
    return len(asyncio.run(mcp.list_tools()))


def test_doc_total_claims_match_real_tool_count() -> None:
    text = DOC.read_text(encoding="utf-8")
    claims = _TOTAL_CLAIM_RE.findall(text)
    assert claims, (
        "docs/mcp-integration.md should claim a tool count via pattern "
        "`**N инструментов**` / `**N тула**`. None found."
    )

    real = _real_tool_count()
    wrong = [c for c in claims if int(c) != real]
    assert not wrong, (
        f"docs/mcp-integration.md claims tool count(s) {wrong}, "
        f"real catalog has {real}. Sync the doc or the catalog."
    )


def test_doc_family_rows_sum_to_itogo() -> None:
    text = DOC.read_text(encoding="utf-8")
    family_counts = [int(n) for n in _FAMILY_ROW_RE.findall(text)]
    total_match = _TOTAL_ROW_RE.search(text)
    assert family_counts, "no family rows found in catalog table"
    assert total_match, "no `**ИТОГО**` row found in catalog table"

    declared_total = int(total_match.group(1))
    summed = sum(family_counts)
    assert summed == declared_total, (
        f"Sum of family rows ({summed}) != ИТОГО ({declared_total}). Family counts: {family_counts}"
    )


def test_doc_itogo_matches_real_catalog() -> None:
    text = DOC.read_text(encoding="utf-8")
    total_match = _TOTAL_ROW_RE.search(text)
    assert total_match, "no `**ИТОГО**` row found in catalog table"
    declared = int(total_match.group(1))
    real = _real_tool_count()
    assert declared == real, (
        f"docs/mcp-integration.md ИТОГО={declared}, real catalog={real}. "
        f"После добавления/удаления тулов обновляй каталог и счётчики."
    )
