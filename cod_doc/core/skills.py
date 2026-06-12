"""Skill-catalog loader (STB-022).

Session-free readers over the ``cod_doc/skills/<name>/SKILL.md`` markdown
catalog. Lives in ``core`` so every layer can import it without inverting the
dependency direction: previously these helpers lived in
``cod_doc.mcp.tools.skill_tools`` and were imported by ``services`` and
``agent`` (a lower layer reaching up into the MCP layer). ``skill_tools`` now
re-exports from here for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Resolve at import time so callers and tests see the same root.
# core/skills.py → parents[1] == cod_doc/ → cod_doc/skills.
SKILLS_ROOT: Path = Path(__file__).resolve().parents[1] / "skills"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return ({name: value, ...}, body) from a YAML-frontmatter markdown.

    Frontmatter is the leading block between ``---\\n`` fences. Only
    top-level scalar key:value pairs and a folded multi-line ``description``
    are needed for the catalog — full YAML parsing isn't worth a yaml dep
    on the read-path. Returns ``({}, text)`` if no fence is present.
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
