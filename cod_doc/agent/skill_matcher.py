"""PCA-002: select skills relevant to a task by keyword-matching against
the SKILL.md frontmatter ``description``.

Минимальный triggered-loader: для каждой задачи берём её title /
description / type, нормализуем регистр + кириллицу, проверяем подстрочно
keywords из ``description`` соответствующего скилла. Возвращаем список
SKILL.md-путей в порядке, в котором их грузит ``Orchestrator`` перед
LLM-вызовом.

Интеграция:
- ``Orchestrator._compose_system_prompt(task)`` собирает строку из
  ``orchestrator/SKILL.md`` + match'нутых триггер-скиллов.
- Match'ер чистый — нет I/O в hot path; все skill-records кешируются на
  первый вызов.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

from cod_doc.mcp.tools.skill_tools import SKILLS_ROOT, iter_skill_records

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.core.project import Task


# Skip the orchestrator base — it's always preloaded by `prompts.py`.
_BASE_SKILL = "orchestrator"


def _normalize(text: str) -> str:
    """Lowercase + strip punctuation for keyword matching.

    Handles both Latin and Cyrillic; we don't transliterate — just remove
    punctuation that would split otherwise-matching tokens.
    """
    text = text.lower()
    text = re.sub(r"[^\wЀ-ӿ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_keywords(description: str) -> list[str]:
    """Pull keyword tokens out of a SKILL.md ``description`` block.

    Strategy: split on commas / semicolons / newlines, trim each token,
    lowercase, filter shorts (<3 chars). The skill author writes triggers
    as a comma-separated list inside the description; we don't try to
    parse English prose. Tokens with internal hyphens / underscores are
    kept as-is so e.g. ``FM-002`` matches.
    """
    raw = description.lower()
    # Strip out the "Триггеры:" prefix / English equivalent from the natural-
    # language part — keywords are typically after that label.
    raw = raw.replace("триггеры:", " ")
    raw = raw.replace("triggers:", " ")
    tokens = re.split(r"[,;\n]+", raw)
    out: list[str] = []
    for tok in tokens:
        cleaned = tok.strip(" .—–-")
        if not cleaned or len(cleaned) < 3:
            continue
        # Drop pure-prose connectives that would over-match.
        if cleaned in {
            "and",
            "the",
            "for",
            "from",
            "with",
            "когда",
            "это",
            "для",
            "при",
            "или",
            "что",
            "etc",
        }:
            continue
        out.append(cleaned)
    return out


@lru_cache(maxsize=1)
def _skill_index() -> list[tuple[str, Path, list[str]]]:
    """Cached: ``[(name, SKILL.md path, [keywords])]`` for triggered skills.

    Built once per process. Cleared by tests via
    :func:`_skill_index.cache_clear` if they mutate the on-disk catalog.
    The orchestrator base is excluded — it's preloaded unconditionally.
    """
    out: list[tuple[str, Path, list[str]]] = []
    for record in iter_skill_records():
        name = record["name"]
        if name == _BASE_SKILL:
            continue
        skill_md = SKILLS_ROOT / name / "SKILL.md"
        kw = _extract_keywords(record.get("description", ""))
        out.append((name, skill_md, kw))
    return out


def select_skills(task: Task) -> list[Path]:
    """Return the list of triggered SKILL.md paths for ``task``.

    Matches on lowercase substrings of ``task.title`` + ``task.description``
    + ``task.kind``. Order of returned paths matches catalog order
    (alphabetical by name). Returns an empty list when no skill triggers —
    caller still gets the orchestrator base from ``prompts.SYSTEM_PROMPT``.
    """
    haystack = _normalize(
        " ".join(
            x
            for x in (
                getattr(task, "title", "") or "",
                getattr(task, "description", "") or "",
                getattr(task, "kind", "") or "",
            )
            if x
        )
    )
    if not haystack:
        return []

    matched: list[Path] = []
    for _name, path, keywords in _skill_index():
        for kw in keywords:
            if kw in haystack:
                matched.append(path)
                break
    return matched


def compose_system_prompt(base: str, triggered_paths: list[Path]) -> str:
    """Return ``base`` + concatenation of triggered SKILL.md bodies.

    Each triggered body is appended after a separator block so the LLM
    sees the structure clearly. SKILL.md frontmatter is stripped (between
    ``---`` fences) — it's metadata for the matcher, not the LLM.
    """
    parts: list[str] = [base.rstrip()]
    for path in triggered_paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end != -1:
                text = text[end + 5 :].lstrip()
        parts.append("\n\n---\n\n" + text.rstrip())
    return "".join(parts)
