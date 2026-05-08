"""PCA-002 / PCA-004: skill matcher activation matrix.

Covers `select_skills` against the 5 triggered SKILL.md files
(validation / audit-cadence / drift-handling / plan-to-tasks / doc-style).
Every skill must (a) trigger on at least one realistic task, (b) NOT
trigger on a task in another skill's domain — guarding against
over-matching.
"""

from __future__ import annotations

from cod_doc.agent.skill_matcher import (
    _extract_keywords,
    _normalize,
    _skill_index,
    compose_system_prompt,
    select_skills,
)
from cod_doc.core.project import Task

# --------------------------------------------------------------------------- #
# Catalog presence                                                              #
# --------------------------------------------------------------------------- #


def test_skill_index_contains_all_5_triggered_skills() -> None:
    """5 non-base skills authored in PCA-002 must register."""
    _skill_index.cache_clear()
    names = {entry[0] for entry in _skill_index()}
    expected = {
        "validation",
        "audit-cadence",
        "drift-handling",
        "plan-to-tasks",
        "doc-style",
    }
    assert expected <= names, f"missing: {expected - names}"
    # Orchestrator base is excluded from the triggered index.
    assert "orchestrator" not in names


def test_each_triggered_skill_has_at_least_one_keyword() -> None:
    _skill_index.cache_clear()
    for name, _, kw in _skill_index():
        assert kw, f"skill {name!r} extracted no keywords"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def test_normalize_lowercases_and_strips_punct() -> None:
    assert _normalize("Hello, World!") == "hello world"
    assert _normalize("FM-002") == "fm 002"
    assert _normalize("  Validate   foo!!!  ") == "validate foo"


def test_extract_keywords_skips_prose_connectives_and_shorts() -> None:
    desc = "Триггеры: validate, foo, the, и, sync\nfor, sync"
    kws = _extract_keywords(desc)
    assert "validate" in kws
    assert "foo" in kws
    assert "sync" in kws
    assert "the" not in kws
    assert "for" not in kws
    assert "и" not in kws  # too short


# --------------------------------------------------------------------------- #
# Activation matrix — positives                                                 #
# --------------------------------------------------------------------------- #


def _names_of(paths) -> set[str]:  # type: ignore[no-untyped-def]
    return {p.parent.name for p in paths}


def test_validation_triggers_on_frontmatter_task() -> None:
    t = Task(title="Add: validate frontmatter rule FM-002", description="structural check")
    assert "validation" in _names_of(select_skills(t))


def test_audit_cadence_triggers_on_section_close() -> None:
    t = Task(
        title="Close section A: write audit-report",
        description="phase complete; produce kickoff brief for next phase",
    )
    assert "audit-cadence" in _names_of(select_skills(t))


def test_drift_handling_triggers_on_hash_mismatch() -> None:
    t = Task(
        title="Investigate drift: stale hash on MASTER",
        description="check_stale_refs reports STALE; need update_master_hashes",
    )
    assert "drift-handling" in _names_of(select_skills(t))


def test_plan_to_tasks_triggers_on_decompose() -> None:
    t = Task(
        title="Decompose plan into tasks for new section",
        description="break the plan down with story_id and acceptance",
    )
    assert "plan-to-tasks" in _names_of(select_skills(t))


def test_doc_style_triggers_on_writing_docs() -> None:
    t = Task(
        title="Update HANDBOOK style: hybrid links and frontmatter",
        description="fix headings and link format",
    )
    assert "doc-style" in _names_of(select_skills(t))


# --------------------------------------------------------------------------- #
# Activation matrix — negatives                                                 #
# --------------------------------------------------------------------------- #


def test_no_skill_triggers_on_unrelated_task() -> None:
    t = Task(
        title="Implement: weather widget",
        description="add a temperature display to the dashboard",
    )
    assert select_skills(t) == []


def test_validation_does_not_trigger_on_pure_refactor() -> None:
    t = Task(
        title="Refactor: extract helper from orchestrator loop",
        description="no behavior change",
    )
    assert "validation" not in _names_of(select_skills(t))


def test_audit_cadence_does_not_trigger_on_routine_feature() -> None:
    t = Task(
        title="Implement: weather widget polish",
        description="visual tweaks",
    )
    assert "audit-cadence" not in _names_of(select_skills(t))


def test_drift_handling_does_not_trigger_on_unrelated_task() -> None:
    t = Task(
        title="Add: new MCP tool for user listing",
        description="iterate users, return list",
    )
    assert "drift-handling" not in _names_of(select_skills(t))


def test_select_skills_empty_task_returns_empty_list() -> None:
    t = Task(title="", description="")
    assert select_skills(t) == []


# --------------------------------------------------------------------------- #
# compose_system_prompt                                                        #
# --------------------------------------------------------------------------- #


def test_compose_system_prompt_appends_triggered_bodies_without_frontmatter() -> None:
    t = Task(
        title="Add: validate frontmatter rule",
        description="FM-002 structural check",
    )
    paths = select_skills(t)
    assert paths, "expected validation to trigger"
    prompt = compose_system_prompt("BASE", paths)
    assert prompt.startswith("BASE")
    # Triggered body is included.
    assert "Validation pattern" in prompt
    # Frontmatter fence is NOT present in the composed prompt.
    assert "---\nname:" not in prompt


def test_compose_system_prompt_no_triggers_returns_base_only() -> None:
    prompt = compose_system_prompt("BASE", [])
    assert prompt == "BASE"
