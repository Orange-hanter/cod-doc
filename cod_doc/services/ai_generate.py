"""COD-060/068/069: structured AI generation flows.

Three pipelines, all returning *drafts* — never persists by itself. The web
route is responsible for showing the preview to the user and committing only
what the user accepts.

Each function delegates the LLM call to a small private ``_chat_json`` helper
that asks the model to reply with strict JSON, parses, and surfaces errors
as ``AIBackendError`` (so routes show one consistent inline-failure pattern,
matching ai_text.improve_text).

Tests monkey-patch ``_chat_json`` to avoid network: see tests/services/
test_ai_generate.py for the expected payload shapes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cod_doc.services.ai_text import AIBackendError

if TYPE_CHECKING:
    from cod_doc.config import Config


# ── Draft DTOs ─────────────────────────────────────────────────────────────


@dataclass
class StoryDraft:
    persona: str
    narrative: str  # "I want X so that Y"
    priority: str = "medium"  # critical | high | medium | low
    acceptance: list[str] = field(default_factory=list)


@dataclass
class TaskDraft:
    title: str
    type: str = "feature"  # feature | test | bug | refactor | migration | docs | chore
    priority: str = "medium"
    section_letter: str | None = None
    description: str | None = None


@dataclass
class MasterDraft:
    """Result of a folder scan: the proposed MASTER.md + coverage gaps."""

    master_md: str
    coverage_tasks: list[TaskDraft] = field(default_factory=list)
    files_seen: list[str] = field(default_factory=list)


@dataclass
class DocDraft:
    """COD-078: a proposed Document built from existing-doc context.

    Sections are ordered tuples of ``(heading, body)`` ready to feed into
    ``doc_service.add_section``.
    """

    doc_key: str
    title: str
    type: str = "module-spec"
    preamble: str = ""
    sections: list[tuple[str, str]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


@dataclass
class GenerationMeta:
    """Token + duration stats for trace logging."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0


# ── Public generation functions ────────────────────────────────────────────


_STORY_SYSTEM_PROMPT = (
    "You are a product analyst. Given documentation excerpts, produce a list "
    "of user stories that would cover the functionality described.\n\n"
    "Reply with ONLY a JSON object of the shape:\n"
    '  {"stories": [{"persona": str, "narrative": str, "priority": '
    '"critical"|"high"|"medium"|"low", "acceptance": [str, ...]}, ...]}\n\n'
    "Rules:\n"
    "- narrative MUST follow the form 'I want X so that Y'.\n"
    "- 1-5 acceptance criteria per story (testable, concrete).\n"
    "- 3-8 stories total — quality over quantity.\n"
    "- Preserve the original language of the documentation."
)


_TASK_SYSTEM_PROMPT = (
    "You are a tech lead decomposing a user story into engineering tasks.\n\n"
    "Reply with ONLY a JSON object of the shape:\n"
    '  {"tasks": [{"title": str, "type": "feature"|"test"|"bug"|"refactor"|'
    '"migration"|"docs"|"chore", "priority": "critical"|"high"|"medium"|"low", '
    '"section_letter": str|null, "description": str|null}, ...]}\n\n'
    "Rules:\n"
    "- Each task is self-contained (2-4 hours of work).\n"
    "- Include at least one test-type task per story.\n"
    "- Pick section_letter from the provided plan layout when given; else leave null.\n"
    "- Prefer imperative title in English: 'Implement X', 'Test Y', 'Migrate Z'."
)


_DOC_SYSTEM_PROMPT = (
    "You are a documentation author. The user gives you a set of existing "
    "project docs as context and an intent for the new doc to write. Produce "
    "a structured document that builds on (without duplicating) the source "
    "material.\n\n"
    "Reply with ONLY a JSON object of the shape:\n"
    '  {"doc_key": str, "title": str, "type": "module-spec"|"module-subdoc"|'
    '"execution-plan"|"task-section"|"execution-log"|"standard"|"architecture"|'
    '"vision"|"guide"|"user-story"|"decision"|"open-question"|"redirect", '
    '"preamble": str, "sections": [{"heading": str, "body": str}, ...]}\n\n'
    "Rules:\n"
    "- doc_key uses kebab/slash style (no spaces, e.g. 'guides/onboarding').\n"
    "- 2-6 sections, each with a non-empty heading and a markdown body.\n"
    "- Preserve the language of the source documents.\n"
    "- Do NOT verbatim-copy source paragraphs; cite them in the preamble."
)


_MASTER_SYSTEM_PROMPT = (
    "You are a documentation architect. Given a list of project files (path "
    "+ excerpt of each), produce a MASTER.md index that:\n\n"
    "- Lists modular sections grouped by area.\n"
    "- Lists the existing files with one-line purpose each.\n"
    "- Has a 'Coverage gaps' section flagging missing topics.\n\n"
    "Reply with ONLY a JSON object of the shape:\n"
    '  {"master_md": str, "coverage_tasks": [{"title": str, "type": '
    '"docs"|"feature", "priority": "high"|"medium"|"low", "section_letter": str|null, '
    '"description": str|null}, ...]}\n\n'
    "Rules:\n"
    "- master_md is full markdown text (with leading '# Project Master').\n"
    "- coverage_tasks describe what is missing — 0-10 items."
)


def generate_stories(
    docs_text: str,
    *,
    cfg: Config,
    intent: str = "",
) -> tuple[list[StoryDraft], GenerationMeta]:
    """Produce StoryDraft list from concatenated documentation excerpts."""
    if not docs_text.strip():
        raise AIBackendError("Nothing to read — docs are empty.")
    user_msg = (
        f"Intent: {intent.strip() or '(default)'}\n\nDocs:\n{docs_text[:60_000]}"
    )
    payload, meta = _chat_json(_STORY_SYSTEM_PROMPT, user_msg, cfg=cfg)
    if "stories" not in payload or not isinstance(payload["stories"], list):
        raise AIBackendError("LLM payload missing 'stories' array.")
    try:
        drafts = [_coerce_story(item) for item in payload["stories"]]
    except (TypeError, AttributeError) as exc:  # COD-077: bad shape = bus-error
        raise AIBackendError(f"LLM stories payload malformed: {exc}") from exc
    return drafts, meta


def generate_tasks_for_story(
    story_persona: str,
    story_narrative: str,
    *,
    cfg: Config,
    section_layout: list[tuple[str, str]] | None = None,
    intent: str = "",
) -> tuple[list[TaskDraft], GenerationMeta]:
    """Produce TaskDraft list for a single user story.

    section_layout is an optional list of (letter, title) pairs the model can
    pick from when assigning section_letter.
    """
    layout_str = (
        "Sections: "
        + ", ".join(f"{letter}={title}" for letter, title in (section_layout or []))
        if section_layout
        else "Sections: (none provided — leave section_letter null)"
    )
    user_msg = (
        f"Intent: {intent.strip() or '(default)'}\n"
        f"{layout_str}\n\n"
        f"Story persona: {story_persona}\n"
        f"Story narrative: {story_narrative}"
    )
    payload, meta = _chat_json(_TASK_SYSTEM_PROMPT, user_msg, cfg=cfg)
    if "tasks" not in payload or not isinstance(payload["tasks"], list):
        raise AIBackendError("LLM payload missing 'tasks' array.")
    try:
        drafts = [_coerce_task(item) for item in payload["tasks"]]
    except (TypeError, AttributeError) as exc:
        raise AIBackendError(f"LLM tasks payload malformed: {exc}") from exc
    return drafts, meta


def generate_doc_from_sources(
    sources: list[tuple[str, str]],
    *,
    cfg: Config,
    intent: str = "",
    target_type: str = "module-spec",
) -> tuple[DocDraft, GenerationMeta]:
    """COD-078: produce a DocDraft seeded by ``sources`` + ``intent``.

    ``sources`` is a list of ``(doc_key, body)`` pairs — caller supplies
    rendered bodies; the prompt uses them verbatim as context.
    """
    if not sources:
        raise AIBackendError("No source documents selected.")
    excerpt = "\n\n".join(
        f"## {key}\n```\n{body[:6000]}\n```" for key, body in sources[:8]
    )
    user_msg = (
        f"Intent: {intent.strip() or '(default)'}\n"
        f"Preferred type: {target_type or '(default)'}\n\n"
        f"Source documents:\n{excerpt}"
    )
    payload, meta = _chat_json(_DOC_SYSTEM_PROMPT, user_msg, cfg=cfg)

    doc_key = str(payload.get("doc_key") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not doc_key or not title:
        raise AIBackendError("LLM payload missing doc_key/title.")
    raw_type = str(payload.get("type") or target_type).strip().lower()
    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list):
        raise AIBackendError("LLM payload missing 'sections' array.")
    sections: list[tuple[str, str]] = []
    try:
        for item in raw_sections:
            if not isinstance(item, dict):
                continue
            heading = str(item.get("heading") or "").strip()
            body = str(item.get("body") or "").strip()
            if heading:
                sections.append((heading, body))
    except (TypeError, AttributeError) as exc:
        raise AIBackendError(f"LLM sections payload malformed: {exc}") from exc

    draft = DocDraft(
        doc_key=doc_key,
        title=title,
        type=raw_type,
        preamble=str(payload.get("preamble") or "").strip(),
        sections=sections,
        sources=[k for k, _ in sources],
    )
    return draft, meta


def generate_master_from_folder(
    files: list[tuple[str, str]],
    *,
    cfg: Config,
    intent: str = "",
) -> tuple[MasterDraft, GenerationMeta]:
    """Produce a MasterDraft (proposed MASTER.md + coverage gap tasks).

    ``files`` is a list of (relative_path, excerpt) — the caller is in charge
    of walking the folder, filtering by extension, and trimming each file to
    a reasonable size.
    """
    if not files:
        raise AIBackendError("No files to scan.")
    excerpt = "\n\n".join(
        f"## {path}\n```\n{body[:2000]}\n```" for path, body in files[:40]
    )
    user_msg = f"Intent: {intent.strip() or '(default)'}\n\nFiles:\n{excerpt}"
    payload, meta = _chat_json(_MASTER_SYSTEM_PROMPT, user_msg, cfg=cfg)
    master_md = payload.get("master_md") or ""
    if not isinstance(master_md, str) or not master_md.strip():
        raise AIBackendError("LLM payload missing 'master_md' string.")
    raw_tasks = payload.get("coverage_tasks") or []
    if not isinstance(raw_tasks, list):
        raw_tasks = []
    try:
        coverage = [_coerce_task(item) for item in raw_tasks]
    except (TypeError, AttributeError) as exc:
        raise AIBackendError(
            f"LLM coverage_tasks payload malformed: {exc}"
        ) from exc
    return (
        MasterDraft(
            master_md=master_md.strip(),
            coverage_tasks=coverage,
            files_seen=[p for p, _ in files],
        ),
        meta,
    )


# ── Internal helpers ───────────────────────────────────────────────────────


def _chat_json(
    system_prompt: str, user_msg: str, *, cfg: Config
) -> tuple[dict[str, Any], GenerationMeta]:
    """Run a single chat completion that must return JSON; parse + return."""
    if not cfg.api_key:
        raise AIBackendError("LLM backend not configured: set the API key in /settings.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIBackendError("openai package is not installed.") from exc

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    started = time.monotonic()
    try:
        completion = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=cfg.max_tokens,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # network, rate-limit, etc.
        raise AIBackendError(f"LLM call failed: {exc}") from exc
    duration_ms = int((time.monotonic() - started) * 1000)

    raw_content = (completion.choices[0].message.content or "").strip()
    if not raw_content:
        raise AIBackendError("LLM returned empty content.")
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise AIBackendError(f"LLM did not return valid JSON: {exc}") from exc

    usage = getattr(completion, "usage", None)
    meta = GenerationMeta(
        model=cfg.model,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        duration_ms=duration_ms,
    )
    return payload, meta


_VALID_PRIORITIES = {"critical", "high", "medium", "low"}
_VALID_TASK_TYPES = {"feature", "test", "bug", "refactor", "migration", "docs", "chore"}


def _coerce_story(item: Any) -> StoryDraft:
    if not isinstance(item, dict):
        raise AIBackendError(f"Bad story item: {item!r}")
    persona = str(item.get("persona") or "").strip()
    narrative = str(item.get("narrative") or "").strip()
    if not persona or not narrative:
        raise AIBackendError("Story is missing persona or narrative.")
    priority = str(item.get("priority") or "medium").lower()
    if priority not in _VALID_PRIORITIES:
        priority = "medium"
    raw_acc = item.get("acceptance") or []
    acceptance = [str(a).strip() for a in raw_acc if str(a).strip()]
    return StoryDraft(
        persona=persona, narrative=narrative, priority=priority, acceptance=acceptance
    )


def _coerce_task(item: Any) -> TaskDraft:
    if not isinstance(item, dict):
        raise AIBackendError(f"Bad task item: {item!r}")
    title = str(item.get("title") or "").strip()
    if not title:
        raise AIBackendError("Task draft is missing title.")
    type_ = str(item.get("type") or "feature").lower()
    if type_ not in _VALID_TASK_TYPES:
        type_ = "feature"
    priority = str(item.get("priority") or "medium").lower()
    if priority not in _VALID_PRIORITIES:
        priority = "medium"
    section_letter = item.get("section_letter")
    if section_letter is not None:
        section_letter = str(section_letter).strip().upper() or None
    description = item.get("description")
    if description is not None:
        description = str(description).strip() or None
    return TaskDraft(
        title=title,
        type=type_,
        priority=priority,
        section_letter=section_letter,
        description=description,
    )
