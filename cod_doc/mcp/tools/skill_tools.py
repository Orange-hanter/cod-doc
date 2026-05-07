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

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


# Resolve at import time so callers and tests see the same root.
SKILLS_ROOT: Path = Path(__file__).resolve().parents[2] / "skills"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return ({name: value, ...}, body) from a YAML-frontmatter markdown.

    Frontmatter is the leading block between ``---\\n`` fences. Only
    top-level scalar key:value pairs and a folded multi-line ``description``
    are needed for the catalog — full YAML parsing isn't worth a yaml dep
    on the MCP read-path. Returns ``({}, text)`` if no fence is present.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    block = text[4:end]
    body = text[end + 5 :].lstrip()

    out: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for raw_line in block.splitlines():
        if raw_line.startswith(("  ", "\t")) and current_key is not None:
            current_lines.append(raw_line.strip())
            continue
        if current_key is not None:
            out[current_key] = " ".join(current_lines).strip()
            current_key = None
            current_lines = []
        if ":" not in raw_line:
            continue
        key, _, value = raw_line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in ("|", ">", "|+", ">+", "|-", ">-"):
            current_key = key
            current_lines = []
        else:
            out[key] = value
    if current_key is not None:
        out[current_key] = " ".join(current_lines).strip()
    return out, body


def iter_skill_records() -> list[dict[str, Any]]:
    """Walk ``skills/<name>/SKILL.md`` files and return catalog entries.

    Returns ``[{name, description, path}]`` sorted by name. Unknown / missing
    frontmatter fields are reported as empty strings rather than failing —
    the catalog should always render even if a skill is being authored.
    """
    if not SKILLS_ROOT.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for skill_dir in sorted(p for p in SKILLS_ROOT.iterdir() if p.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        text = skill_md.read_text(encoding="utf-8")
        meta, _body = _parse_frontmatter(text)
        records.append(
            {
                "name": meta.get("name") or skill_dir.name,
                "description": meta.get("description", ""),
                "path": str(skill_md.relative_to(SKILLS_ROOT.parent.parent)),
            }
        )
    return records


def get_skill_record(name: str) -> dict[str, Any] | None:
    """Return ``{name, description, path, body}`` for one skill, or None."""
    skill_md = SKILLS_ROOT / name / "SKILL.md"
    if not skill_md.is_file():
        return None
    text = skill_md.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    return {
        "name": meta.get("name") or name,
        "description": meta.get("description", ""),
        "path": str(skill_md.relative_to(SKILLS_ROOT.parent.parent)),
        "body": body,
    }


def register(mcp: FastMCP) -> None:
    """Register skill.* tools on the given FastMCP instance."""

    @mcp.tool(name="skill.list")
    def skill_list() -> list[dict[str, Any]]:
        """List the agent skill catalog (name + description + path).

        Each entry comes from a ``cod_doc/skills/<name>/SKILL.md``
        YAML-frontmatter. Sorted by name. Body is omitted to keep the
        listing compact — call ``skill.get`` for the full markdown.
        """
        return iter_skill_records()

    @mcp.tool(name="skill.get")
    def skill_get(name: str) -> dict[str, Any] | None:
        """Return one skill's full body (markdown without YAML fences).

        Returns ``None`` for unknown ``name``.
        """
        return get_skill_record(name)
