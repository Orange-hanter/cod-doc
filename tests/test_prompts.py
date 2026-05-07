"""PCA-001: orchestrator SKILL.md → SYSTEM_PROMPT thin assembler."""

from __future__ import annotations

from pathlib import Path

import pytest

from cod_doc.agent import prompts as prompts_mod
from cod_doc.agent.prompts import SYSTEM_PROMPT, _strip_frontmatter

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / "cod_doc" / "skills" / "orchestrator" / "SKILL.md"


def test_orchestrator_skill_file_exists() -> None:
    assert SKILL_PATH.is_file(), f"missing SKILL.md at {SKILL_PATH}"


def test_orchestrator_skill_has_yaml_frontmatter_with_name_and_description() -> None:
    raw = SKILL_PATH.read_text(encoding="utf-8")
    assert raw.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    end = raw.find("\n---\n", 4)
    assert end != -1, "frontmatter must close with --- fence"
    fm = raw[4:end]
    assert "name: orchestrator" in fm
    assert "description:" in fm


def test_strip_frontmatter_removes_fenced_block() -> None:
    raw = "---\nname: x\ndescription: y\n---\n\nbody text\n"
    assert _strip_frontmatter(raw) == "body text\n"


def test_strip_frontmatter_passes_through_when_no_fence() -> None:
    raw = "no frontmatter here\nbody\n"
    assert _strip_frontmatter(raw) == raw


def test_system_prompt_drops_yaml_and_keeps_body() -> None:
    assert "---\nname:" not in SYSTEM_PROMPT
    assert "description:" not in SYSTEM_PROMPT.split("\n", 5)[0]
    assert "COD-DOC Orchestrator" in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "section",
    [
        "Snowball Protocol",
        "Гибридные ссылки",
        "Алгоритм выполнения задачи",
        "Fail-Fast",
        "self_check",
    ],
)
def test_system_prompt_contains_required_section(section: str) -> None:
    assert section in SYSTEM_PROMPT, f"required section missing: {section!r}"


def test_prompts_module_is_thin_loader() -> None:
    """PCA-001 acceptance: prompts.py must remain ≤50 source lines."""
    src = Path(prompts_mod.__file__).read_text(encoding="utf-8").splitlines()
    assert len(src) <= 50, f"prompts.py has {len(src)} lines, expected ≤50"
