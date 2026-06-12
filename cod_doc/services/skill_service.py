"""Read access to the shipped skill catalog.

The web layer is forbidden from importing `cod_doc.infra` or
`cod_doc.mcp` directly (see tests/api/test_web_layer_imports.py).  This
thin service wraps :mod:`cod_doc.mcp.tools.skill_tools` so the catalog
can be listed and inspected from pages / fragments.

Read-only — skills are package-shipped markdown, not runtime state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.core.skills import SKILLS_ROOT, iter_skill_records

if TYPE_CHECKING:
    from pathlib import Path


def list_skills() -> list[dict[str, Any]]:
    """Return the catalog as a list of frontmatter records.

    Each record carries at least ``name``, ``description``, and ``path``
    (path is repo-relative for display).
    """
    return iter_skill_records()


def get_skill(name: str) -> dict[str, Any] | None:
    """Return one skill record by name, or ``None`` if unknown."""
    for r in iter_skill_records():
        if r.get("name") == name:
            return r
    return None


def recommend_for_tool(tool_name: str) -> list[str]:
    """PCA-949: match a tool call against skill trigger keywords.

    Each ``SKILL.md`` frontmatter ``description`` field carries a free-form
    paragraph that typically includes the line ``Триггеры: ...`` listing
    trigger keywords. We split-and-match ``tool_name`` (snake → tokens)
    against those triggers + the skill ``name``. Returns ranked skill
    names (highest match first).

    Cheap and deterministic; agents can fetch full bodies via
    ``skill_get(name)`` based on the recommendation.
    """
    tokens = {t for t in tool_name.lower().split("_") if len(t) >= 3}
    if not tokens:
        return []

    scored: list[tuple[int, str]] = []
    for record in iter_skill_records():
        name = (record.get("name") or "").lower()
        desc = (record.get("description") or "").lower()
        haystack = f"{name} {desc}"
        score = sum(1 for tok in tokens if tok in haystack)
        # The skill's own name matching counts double (e.g. tool task_create
        # → skill task-standard).
        if any(tok in name for tok in tokens):
            score += 2
        if score > 0:
            scored.append((score, record.get("name") or ""))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [name for _, name in scored]


def get_skill_body(name: str) -> str | None:
    """Return the SKILL.md body (frontmatter stripped) or ``None``.

    Used by the standards-browser page to render the full skill content
    after the user expands its summary card.
    """
    skill_md: Path = SKILLS_ROOT / name / "SKILL.md"
    if not skill_md.is_file():
        return None
    text = skill_md.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5 :].lstrip()
    return text
