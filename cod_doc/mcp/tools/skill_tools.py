"""MCP tools: skill.* — agent skill catalog (PCA-003, proposal 01).

Exposes the ``cod_doc/skills/`` markdown catalog over MCP. Until PCA-002
lands the keyword-matcher and the rest of the skill-modules, this surface
is intentionally minimal: list catalog entries (name + description from
each SKILL.md frontmatter), and fetch a full skill body by name.

Module-level helpers (``iter_skill_records``, ``get_skill_record``) are
session-free and unit-testable; the @mcp.tool wrappers add discovery
plumbing for the FastMCP namespace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# STB-022: the catalog loaders moved to ``cod_doc.core.skills`` to fix the
# layering inversion (services/agent importing from mcp.tools). Re-exported
# here so existing importers keep working.
from cod_doc.core.skills import (
    SKILLS_ROOT,
    _parse_frontmatter,
    get_skill_record,
    iter_skill_records,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

__all__ = [
    "SKILLS_ROOT",
    "_parse_frontmatter",
    "get_skill_record",
    "iter_skill_records",
    "register",
]


def register(mcp: FastMCP) -> None:
    """Register skill.* tools on the given FastMCP instance."""

    @mcp.tool(name="skill_list")
    def skill_list() -> list[dict[str, Any]]:
        """List the agent skill catalog (name + description + path).

        Each entry comes from a ``cod_doc/skills/<name>/SKILL.md``
        YAML-frontmatter. Sorted by name. Body is omitted to keep the
        listing compact — call ``skill.get`` for the full markdown.
        """
        return iter_skill_records()

    @mcp.tool(name="skill_get")
    def skill_get(name: str) -> dict[str, Any] | None:
        """Return one skill's full body (markdown without YAML fences).

        Returns ``None`` for unknown ``name``.
        """
        return get_skill_record(name)
