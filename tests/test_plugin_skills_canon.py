"""AFT-013 (RFC 27 F14): плагинные task-flow/doc-sync — единственный канон.

Тексты скиллов читаются с диска, эталоны — литералы. Роль и forbidden
профиля `agent` — те же литералы, что в `tests/test_agent_profile.py`
(AFT-004); из `cod_doc.mcp.tools.agent_tools` сюда ничего не импортируется,
иначе тест сверял бы код сам с собой.
"""

from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "cod-doc" / "skills"
TASK_FLOW = SKILLS_DIR / "task-flow" / "SKILL.md"


def _profile_section(text: str) -> str:
    start = text.index("\n## Профиль\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_no_hardcoded_ids_or_coauthor() -> None:
    skills = sorted(SKILLS_DIR.glob("**/SKILL.md"))
    assert len(skills) >= 2, skills
    banned = ("project_id=1", "plan_id=6", "Co-Authored-By", "make_session_factory")
    offenders = {
        str(p.relative_to(SKILLS_DIR)): hits
        for p in skills
        if (hits := [b for b in banned if b in p.read_text(encoding="utf-8")])
    }
    assert not offenders, offenders


def test_profile_section_matches_capabilities() -> None:
    section = _profile_section(TASK_FLOW.read_text(encoding="utf-8"))
    expected = (
        "agent_capabilities",
        "standard",
        "coder",
        "agent",
        "doc-curator",
        "curator_next",
        "agent_pick",
        "task_checkout",
        "task_complete",
    )
    missing = [e for e in expected if e not in section]
    assert not missing, f"раздел «## Профиль» в task-flow не упоминает: {missing}"


def test_cli_checkout_release_named() -> None:
    text = TASK_FLOW.read_text(encoding="utf-8")
    assert "cod-doc task checkout" in text
    assert "cod-doc task release" in text
    assert "в CLI нет" not in text
