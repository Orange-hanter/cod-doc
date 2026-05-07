"""Системные промпты для агента COD-DOC.

Тонкий сборщик SYSTEM_PROMPT из cod_doc/skills/orchestrator/SKILL.md.
Источник содержимого — markdown-скилл с YAML-frontmatter; здесь — только
загрузка и стрип frontmatter'a. Дополнительные триггерные скиллы
(validation, audit-cadence, drift-handling, ...) подключаются в PCA-002
через select_skills().
"""

from __future__ import annotations

from pathlib import Path

_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "orchestrator" / "SKILL.md"


def _strip_frontmatter(text: str) -> str:
    """Strip leading YAML frontmatter (between '---' fences)."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5 :].lstrip()


def _load_orchestrator_skill() -> str:
    return _strip_frontmatter(_SKILL_PATH.read_text(encoding="utf-8"))


SYSTEM_PROMPT: str = _load_orchestrator_skill()
