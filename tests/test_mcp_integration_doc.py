"""PCA-932: anti-drift — numeric claims in docs/mcp-integration.md
must match the real catalog of the MCP server.

History: the doc promised "23 tools" in six places, while in fact 93 were
already exposed. Any agent / new contributor orienting by this document
built a wrong model of the capabilities.

Test:

1. Reads docs/mcp-integration.md.
2. Extracts the declared total ("**N tools**" / "**N**" in the TOTAL row /
   "N tool for the current release" — three forms are allowed).
3. Compares with ``len(await mcp.list_tools())``.
4. Additionally checks that the family counters in the catalog table
   sum to the total.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from cod_doc.mcp.server import mcp

DOC = Path(__file__).resolve().parents[1] / "docs" / "mcp-integration.md"

# Matches any of:
#   **93 tools**
#   **93 tool**
#   93 tool for the current
# (Russian legacy forms **93 инструментами** / **93 тула** are also matched
# for backward compatibility.)
_TOTAL_CLAIM_RE = re.compile(r"\*\*?(\d{1,4})\s*(?:tool\w*|инструмент\w*|тул\w*)\*?\*?")

# Catalog table row: «| **family** | 12 | ...»
# The family name may contain `.\*` (e.g. `doc.\*`), so the capture group
# allows any characters except pipe.
# Exclude both the English TOTAL row and the Russian ИТОГО row.
_FAMILY_ROW_RE = re.compile(
    r"^\|\s*\*\*(?!TOTAL|ИТОГО)[^|]+?\*\*\s*\|\s*(\d+)\s*\|",
    re.MULTILINE,
)

# The TOTAL row: «| **TOTAL** | **93** | ... |» (English) or
# «| **ИТОГО** | **93** | ... |» (Russian legacy).
_TOTAL_ROW_RE = re.compile(r"\|\s*\*\*(?:TOTAL|ИТОГО)\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|")


def _real_tool_count() -> int:
    return len(asyncio.run(mcp.list_tools()))


def test_doc_total_claims_match_real_tool_count() -> None:
    text = DOC.read_text(encoding="utf-8")
    claims = _TOTAL_CLAIM_RE.findall(text)
    assert claims, (
        "docs/mcp-integration.md should claim a tool count via pattern "
        "`**N tools**` / `**N tool**`. None found."
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
    assert total_match, "no `**TOTAL**` row found in catalog table"

    declared_total = int(total_match.group(1))
    summed = sum(family_counts)
    assert summed == declared_total, (
        f"Sum of family rows ({summed}) != TOTAL ({declared_total}). Family counts: {family_counts}"
    )


def test_doc_itogo_matches_real_catalog() -> None:
    text = DOC.read_text(encoding="utf-8")
    total_match = _TOTAL_ROW_RE.search(text)
    assert total_match, "no `**TOTAL**` row found in catalog table"
    declared = int(total_match.group(1))
    real = _real_tool_count()
    assert declared == real, (
        f"docs/mcp-integration.md TOTAL={declared}, real catalog={real}. "
        f"After adding/removing tools, update the catalog and the counters."
    )
