"""Unit tests for ai_generate — generation pipeline + draft coercion."""

from __future__ import annotations

import pytest

from cod_doc.config import Config
from cod_doc.services import ai_generate
from cod_doc.services.ai_text import AIBackendError


def _cfg() -> Config:
    return Config(
        api_key="sk-test", model="test/m", base_url="https://x"
    )


def _stub_chat_json(payload: dict, monkeypatch) -> None:
    def fake(system_prompt: str, user_msg: str, *, cfg):
        return payload, ai_generate.GenerationMeta(
            model="test/m", input_tokens=10, output_tokens=20, duration_ms=50
        )

    monkeypatch.setattr(ai_generate, "_chat_json", fake)


# ── generate_stories ────────────────────────────────────────────────────


def test_generate_stories_parses_payload(monkeypatch) -> None:
    _stub_chat_json(
        {
            "stories": [
                {
                    "persona": "developer",
                    "narrative": "I want X so that Y",
                    "priority": "high",
                    "acceptance": ["A", "B"],
                },
                {
                    "persona": "ops",
                    "narrative": "I want Z so that W",
                    "priority": "garbage",  # → falls back to medium
                    "acceptance": [],
                },
            ]
        },
        monkeypatch,
    )
    drafts, meta = ai_generate.generate_stories("docs", cfg=_cfg())
    assert len(drafts) == 2
    assert drafts[0].priority == "high"
    assert drafts[0].acceptance == ["A", "B"]
    assert drafts[1].priority == "medium"
    assert meta.input_tokens == 10


def test_generate_stories_rejects_empty_input() -> None:
    with pytest.raises(AIBackendError, match="empty"):
        ai_generate.generate_stories("   ", cfg=_cfg())


def test_generate_stories_requires_stories_array(monkeypatch) -> None:
    _stub_chat_json({"items": []}, monkeypatch)
    with pytest.raises(AIBackendError, match="stories"):
        ai_generate.generate_stories("docs", cfg=_cfg())


def test_generate_stories_validates_required_fields(monkeypatch) -> None:
    _stub_chat_json(
        {"stories": [{"persona": "", "narrative": "I want X"}]},
        monkeypatch,
    )
    with pytest.raises(AIBackendError, match="persona or narrative"):
        ai_generate.generate_stories("docs", cfg=_cfg())


# ── generate_tasks_for_story ────────────────────────────────────────────


def test_generate_tasks_parses_with_section_layout(monkeypatch) -> None:
    _stub_chat_json(
        {
            "tasks": [
                {
                    "title": "Implement search",
                    "type": "feature",
                    "priority": "high",
                    "section_letter": "d",
                    "description": "details",
                },
                {
                    "title": "Test search filter",
                    "type": "test",
                    "priority": "medium",
                    "section_letter": None,
                },
            ]
        },
        monkeypatch,
    )
    drafts, _meta = ai_generate.generate_tasks_for_story(
        "developer",
        "I want search",
        cfg=_cfg(),
        section_layout=[("D", "Search & Tags")],
    )
    assert len(drafts) == 2
    # section_letter is upper-cased
    assert drafts[0].section_letter == "D"
    assert drafts[0].type == "feature"
    assert drafts[1].section_letter is None


def test_generate_tasks_falls_back_on_bad_type(monkeypatch) -> None:
    _stub_chat_json(
        {"tasks": [{"title": "Do thing", "type": "weird", "priority": "low"}]},
        monkeypatch,
    )
    drafts, _ = ai_generate.generate_tasks_for_story(
        "u", "I want X", cfg=_cfg()
    )
    assert drafts[0].type == "feature"  # fallback


# ── generate_master_from_folder ─────────────────────────────────────────


def test_generate_master_returns_md_and_coverage(monkeypatch) -> None:
    _stub_chat_json(
        {
            "master_md": "# Project Master\n\n## Sections\n- A: Core",
            "coverage_tasks": [
                {
                    "title": "Document API auth flow",
                    "type": "docs",
                    "priority": "medium",
                    "section_letter": "A",
                }
            ],
        },
        monkeypatch,
    )
    draft, meta = ai_generate.generate_master_from_folder(
        [("README.md", "hello"), ("arch.md", "world")], cfg=_cfg()
    )
    assert "Project Master" in draft.master_md
    assert len(draft.coverage_tasks) == 1
    assert draft.files_seen == ["README.md", "arch.md"]
    assert meta.model == "test/m"


def test_generate_master_rejects_empty_files() -> None:
    with pytest.raises(AIBackendError, match="No files"):
        ai_generate.generate_master_from_folder([], cfg=_cfg())


def test_generate_master_requires_master_md(monkeypatch) -> None:
    _stub_chat_json({"coverage_tasks": []}, monkeypatch)
    with pytest.raises(AIBackendError, match="master_md"):
        ai_generate.generate_master_from_folder([("a.md", "x")], cfg=_cfg())
