"""Stories list, detail, AI-generation flow (COD-068, COD-069)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_config, get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import (
    Priority,
    StoryLinkKind,
    StoryRelation,
    TaskType,
    UserStoryStatus,
)
from cod_doc.services import ai_generate, trace_service
from cod_doc.services import plan_service as plans
from cod_doc.services import story_service as stories
from cod_doc.services import task_service as task_svc
from cod_doc.services.ai_text import AIBackendError

router = APIRouter()


def _gather_master_text(slug: str) -> str:
    """Read the project's MASTER.md as docs-source for story generation."""
    proj = get_project(slug)
    if proj.entry.master_path.exists():
        return proj.entry.master_path.read_text(encoding="utf-8", errors="replace")
    return ""


def _last_gen_path(slug: str) -> Path:
    """Per-project marker file for the last story-generation time."""

    proj = get_project(slug)
    return proj.entry.cod_doc_dir / "story_last_gen.json"


def _read_last_gen(slug: str) -> datetime | None:
    """Return the timestamp of the last successful story generation, or None."""
    import json
    from datetime import datetime

    path = _last_gen_path(slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return datetime.fromisoformat(data["last_at"])
    except Exception:
        return None


def _write_last_gen(slug: str) -> None:
    """Stamp the marker file — called after a successful generation."""
    import json
    from datetime import UTC, datetime

    path = _last_gen_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_at": datetime.now(UTC).isoformat()}))


def _id_prefix_from_plan_scope(scope: str) -> str:
    """Derive a 2-5 caps prefix from a plan scope like 'cod-doc' → 'COD'."""
    letters = [c for c in scope.upper() if c.isalpha()]
    return "".join(letters[:3]) or "TSK"


# Splits an AI-generated narrative back into its semantic parts so the UI can
# render them with hierarchy instead of a wall of text. The canonical shape we
# emit is "[Section-N.M Title] As a Role, I want X, so that Y" — but the model
# does occasionally drop the section header or use "As an", so the regex makes
# the framing optional and accepts both articles.
_NARRATIVE_PARSER = re.compile(
    r"^\s*"
    r"(?:\[\s*(?P<header>[^\]]+)\]\s*)?"  # optional [Section-N.M Title]
    r"(?:As an?\s+(?P<role>[^,]+?),\s*)?"  # optional "As a/an Role,"
    r"I want\s+(?P<want>.+?)"
    r",\s*so that\s+(?P<so_that>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Header inside the brackets is sometimes "US-1.1 Title", sometimes just "Title".
_HEADER_SPLIT = re.compile(r"^([A-Z]{2,5}-?\d+(?:\.\d+)?)\s+(.+)$")


def _parse_narrative(text: str) -> dict[str, str]:
    """Split a user-story narrative into header / role / want / so_that.

    Returns the empty-string default for any part the parser cannot find;
    the template falls back to ``raw`` when the parse fails entirely.
    """
    if not text:
        return {"raw": "", "id_hint": "", "title": "", "role": "", "want": "", "so_that": ""}
    m = _NARRATIVE_PARSER.match(text)
    if not m:
        return {"raw": text, "id_hint": "", "title": "", "role": "", "want": "", "so_that": ""}
    header = (m.group("header") or "").strip()
    id_hint = ""
    title = header
    hm = _HEADER_SPLIT.match(header)
    if hm:
        id_hint = hm.group(1)
        title = hm.group(2).strip()
    return {
        "raw": text,
        "id_hint": id_hint,
        "title": title,
        "role": (m.group("role") or "").strip(),
        "want": (m.group("want") or "").strip(),
        "so_that": (m.group("so_that") or "").strip(),
    }


# Stable palette for persona chips — assigned by hash so the same persona keeps
# the same colour across renders. Aligned with the design-token CSS variables.
_PERSONA_HUES = ("accent", "purple", "success", "warning", "danger")


def _persona_hue(persona: str) -> str:
    if not persona:
        return _PERSONA_HUES[0]
    return _PERSONA_HUES[sum(ord(c) for c in persona) % len(_PERSONA_HUES)]


# ── Stories list ───────────────────────────────────────────────────────


# US-1.x → "1", US-1 → "1", anything else → "?".
_SECTION_PREFIX = re.compile(r"^([A-Z]{2,5}-?)(\d+)(?:\.\d+)?")


def _section_of(id_hint: str) -> str:
    m = _SECTION_PREFIX.match(id_hint or "")
    return m.group(2) if m else ""


@router.get("/p/{slug}/stories", response_class=HTMLResponse)
def stories_list(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    group_by: str = "section",
) -> HTMLResponse:
    """Stories list with parsed narratives, task counts, plan chips, grouping.

    ``group_by`` ∈ {section, persona, none}: section groups by US-N (the
    section number in the id_hint), persona by literal persona name, none
    shows a single flat grid.
    """
    from cod_doc.services import doc_service as docs_svc

    proj = get_project(slug)
    session, project_db_id = db
    rows = stories.list_for_project(session, project_db_id)

    # Doc-context hints: which docs the AI will see + which are newer than the
    # last generation, so the user can spot stale / missing context BEFORE
    # spending tokens.
    last_gen = _read_last_gen(slug)
    context_docs: list[dict[str, Any]] = []
    context_docs_fresh = 0
    for doc in docs_svc.list_for_project(session, project_db_id):
        is_fresh = bool(last_gen and doc.last_updated and doc.last_updated > last_gen)
        if is_fresh:
            context_docs_fresh += 1
        context_docs.append(
            {
                "doc_key": doc.doc_key,
                "type": doc.type.value,
                "status": doc.status.value,
                "updated_at": doc.last_updated.isoformat() if doc.last_updated else "",
                "is_fresh": is_fresh,
            }
        )

    # Group docs by type for the inventory display — each bucket pairs with a
    # role label from doc_type_guides so the user knows what that type is FOR.
    # Sort buckets in priority order: types that materially help story
    # generation (vision, architecture, module-spec, guide) come first.
    _type_priority = {
        "vision": 1,
        "architecture": 2,
        "module-spec": 3,
        "module-subdoc": 4,
        "guide": 5,
        "standard": 6,
        "adr": 7,
        "decision": 8,
        "execution-plan": 9,
        "user-story": 10,
        "open-question": 11,
        "execution-log": 12,
        "task-section": 13,
        "redirect": 14,
    }
    _type_roles = {
        "vision": "Strategic intent — purpose, audience, goals.",
        "architecture": "System structure, components, technology choices.",
        "module-spec": "Implementation-grade module details, interfaces, data model.",
        "module-subdoc": "Deep dive into one aspect of a module.",
        "guide": "How-to walkthroughs and onboarding.",
        "standard": "Normative rules and conventions.",
        "adr": "Architecture Decision Records.",
        "decision": "Single design / product choices.",
        "execution-plan": "Multi-task initiative plans.",
        "user-story": "User-facing requirements.",
        "open-question": "Unresolved technical questions.",
        "execution-log": "Chronological journal of shipped work.",
        "task-section": "Coherent groups of implementation tasks.",
        "redirect": "Stubs pointing at canonical homes.",
    }
    by_type: dict[str, list[dict[str, Any]]] = {}
    for ctx_doc in context_docs:
        by_type.setdefault(ctx_doc["type"], []).append(ctx_doc)
    # Sort docs within each type bucket by recency, then put fresh ones first.
    # Two-pass with a stable sort: the second pass becomes the primary key.
    for type_docs in by_type.values():
        type_docs.sort(key=lambda x: x["updated_at"] or "", reverse=True)
        type_docs.sort(key=lambda x: not x["is_fresh"])

    context_types: list[dict[str, Any]] = [
        {
            "type": t,
            "role": _type_roles.get(t, ""),
            "docs": by_type[t],
            "fresh_count": sum(1 for d in by_type[t] if d["is_fresh"]),
        }
        for t in sorted(by_type.keys(), key=lambda x: _type_priority.get(x, 99))
    ]

    # Coverage cache — populated by POST /stories/coverage/analyze, displayed
    # inline so the user can see prior recommendations without re-running.
    coverage_path = proj.entry.cod_doc_dir / "context_coverage.json"
    coverage: dict[str, Any] | None = None
    if coverage_path.exists():
        try:
            coverage = json.loads(coverage_path.read_text())
        except Exception:
            coverage = None

    # Lookups for task counts + the set of plans linked tasks live in.
    # One pass per story — N+1, but story counts in real projects are small.
    enriched: list[dict[str, Any]] = []
    for s in rows:
        linked = stories.list_tasks(session, s.story_id) if s.row_id else []
        tasks_total = len(linked)
        tasks_done = sum(1 for t in linked if t.status.value == "done")
        plan_ids_seen: set[int] = set()
        plan_scopes: list[str] = []
        for t in linked:
            if t.plan_id in plan_ids_seen:
                continue
            plan_ids_seen.add(t.plan_id)
            plan = plans.get_for_project(session, project_db_id, t.plan_id)
            if plan is not None and plan.scope:
                plan_scopes.append(plan.scope)

        parsed = _parse_narrative(s.narrative)
        section = _section_of(parsed.get("id_hint", "")) or "?"

        enriched.append(
            {
                "story_id": s.story_id,
                "persona": s.persona,
                "persona_hue": _persona_hue(s.persona),
                "narrative": s.narrative,
                "parsed": parsed,
                "section": section,
                "status": s.status.value,
                "priority": s.priority.value,
                "tasks_total": tasks_total,
                "tasks_done": tasks_done,
                "plan_scopes": plan_scopes,
            }
        )

    # Build groups for the template — always emit a sorted list so the order
    # is deterministic across renders.
    from cod_doc.services import section_summary_service as summaries

    summary_path = proj.entry.cod_doc_dir / "section_summaries.json"
    groups: list[dict[str, Any]] = []
    if group_by == "persona":
        persona_bucket: dict[str, list[dict[str, Any]]] = {}
        for st in enriched:
            persona_bucket.setdefault(st["persona"], []).append(st)
        for key in sorted(persona_bucket.keys()):
            groups.append(
                {
                    "key": key,
                    "label": key,
                    "hue": _persona_hue(key),
                    "stories": persona_bucket[key],
                    "summary": None,
                }
            )
    elif group_by == "none":
        groups = [
            {
                "key": "",
                "label": "",
                "hue": "accent",
                "stories": enriched,
                "summary": None,
            }
        ]
    else:  # section (default)
        section_bucket: dict[str, list[dict[str, Any]]] = {}
        for st in enriched:
            section_bucket.setdefault(st["section"], []).append(st)

        # Natural sort: numeric sections first ("1", "2", …), then "?" last.
        def _sort_key(k: str) -> tuple[int, str]:
            return (0 if k.isdigit() else 1, f"{int(k):04}" if k.isdigit() else k)

        # Load all section summaries in one pass (cheap JSON read).
        section_map = {sec: [s["story_id"] for s in items] for sec, items in section_bucket.items()}
        loaded_summaries = summaries.load_all(summary_path, section_map)

        for key in sorted(section_bucket.keys(), key=_sort_key):
            label = f"Section {key}" if key.isdigit() else "Unsorted"
            summary = loaded_summaries.get(key)
            groups.append(
                {
                    "key": key,
                    "label": label,
                    "hue": "accent",
                    "stories": section_bucket[key],
                    "summary": (
                        {
                            "text": summary.text,
                            "generated_at": summary.generated_at,
                            "stale": summary.stale,
                        }
                        if summary
                        else None
                    ),
                }
            )

    return templates.TemplateResponse(
        request,
        "project/stories_list.html",
        {
            "project": {"name": proj.entry.name},
            "stories": enriched,
            "groups": groups,
            "group_by": group_by if group_by in ("section", "persona", "none") else "section",
            "totals": {
                "stories": len(enriched),
                "draft": sum(1 for s in enriched if s["status"] == "draft"),
                "active": sum(1 for s in enriched if s["status"] == "active"),
            },
            "context_docs": context_docs,
            "context_docs_fresh": context_docs_fresh,
            "context_types": context_types,
            "coverage": coverage,
            "last_gen_at": last_gen.isoformat() if last_gen else "",
        },
    )


# ── AI: generate stories from docs ─────────────────────────────────────


@router.post("/p/{slug}/stories/generate", response_class=HTMLResponse)
def stories_generate(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    intent: str = Form(""),
) -> Response:
    """Run the AI generator over MASTER.md, return a draft-preview fragment."""
    proj = get_project(slug)
    session, _project_db_id = db
    cfg = get_config()
    docs_text = _gather_master_text(slug)

    try:
        drafts, meta = ai_generate.generate_stories(docs_text, cfg=cfg, intent=intent)
        notice = f"{len(drafts)} stories proposed — review and save."
        trace_service.record(
            session,
            model=meta.model,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            duration_ms=meta.duration_ms,
            tool_calls=[{"name": "generate_stories"}],
        )
        session.commit()
        # Stamp the marker so the next stories-list render can highlight which
        # docs have changed since the AI last "saw" the project.
        _write_last_gen(slug)
    except AIBackendError as exc:
        drafts = []
        notice = f"AI error: {exc}"
        trace_service.record(session, model=cfg.model, error=str(exc))
        session.commit()

    return templates.TemplateResponse(
        request,
        "_frag/story_drafts.html",
        {
            "project": {"name": proj.entry.name},
            "drafts": [
                {
                    "i": i,
                    "persona": d.persona,
                    "narrative": d.narrative,
                    "priority": d.priority,
                    "acceptance": d.acceptance,
                }
                for i, d in enumerate(drafts)
            ],
            "intent": intent,
            "notice": notice,
        },
    )


@router.post("/p/{slug}/stories/save", response_class=HTMLResponse)
async def stories_save(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """Persist the selected story drafts."""
    proj = get_project(slug)
    session, project_db_id = db
    form = await request.form()

    selected = set(form.getlist("selected"))
    personas = form.getlist("persona")
    narratives = form.getlist("narrative")
    priorities = form.getlist("priority")
    acc_blobs = form.getlist("acceptance")

    saved = 0
    for i, persona in enumerate(personas):
        if str(i) not in selected:
            continue
        narrative = narratives[i] if i < len(narratives) else ""
        priority = priorities[i] if i < len(priorities) else "medium"
        acc_text = acc_blobs[i] if i < len(acc_blobs) else ""
        acceptance = [ln.strip() for ln in str(acc_text).splitlines() if ln.strip()]
        # COD-071: savepoint per draft so a single bad row doesn't roll
        # back the previously-created stories in this batch.
        try:
            with session.begin_nested():
                story_id = stories.next_story_id(session, project_db_id)
                stories.create(
                    session,
                    project_id=project_db_id,
                    story_id=story_id,
                    persona=str(persona).strip(),
                    narrative=str(narrative).strip(),
                    priority=Priority(str(priority)),
                    author="human:web",
                    status=UserStoryStatus.DRAFT,
                    acceptance=acceptance,
                    reason="ai-generate",
                )
        except Exception:
            continue
        saved += 1
    session.commit()
    return RedirectResponse(url=f"/p/{proj.entry.name}/stories?saved={saved}", status_code=303)


# ── Story detail + task generation (COD-069) ──────────────────────────


@router.get("/p/{slug}/stories/{story_id}", response_class=HTMLResponse)
def story_show(
    request: Request,
    slug: str,
    story_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    proj = get_project(slug)
    session, project_db_id = db
    story = stories.get(session, story_id)
    if story is None or story.project_id != project_db_id:
        raise HTTPException(404, f"Story не найдена: {story_id}")
    assert story.row_id is not None

    acceptance = stories.list_acceptance(session, story_id)
    linked_tasks = stories.list_tasks(session, story_id)
    return templates.TemplateResponse(
        request,
        "project/story_show.html",
        {
            "project": {"name": proj.entry.name},
            "story": {
                "story_id": story.story_id,
                "persona": story.persona,
                "narrative": story.narrative,
                "status": story.status.value,
                "priority": story.priority.value,
            },
            "acceptance": [
                {"position": a.position, "criterion": a.criterion, "met": a.met} for a in acceptance
            ],
            "linked_tasks": [
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "status": t.status.value,
                }
                for t in linked_tasks
            ],
            "linked_done": sum(1 for t in linked_tasks if t.status.value == "done"),
            "linked_total": len(linked_tasks),
        },
    )


@router.post(
    "/p/{slug}/stories/coverage/analyze",
    response_class=HTMLResponse,
)
def stories_coverage_analyze(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """Run AI coverage analysis over the project's doc inventory.

    Returns a fragment with structured recommendations:
    {"summary": str, "gaps": [str], "strengths": [str], "recommendation": str}
    The result is cached in ``.cod-doc/context_coverage.json`` so subsequent
    page loads can render it without re-running the AI call.
    """
    from cod_doc.services import doc_service as docs_svc
    from cod_doc.services.ai_text import _call_lite_raw

    proj = get_project(slug)
    session, project_db_id = db
    cfg = get_config()

    # Build a compact inventory snapshot for the prompt.
    inventory_lines = []
    for d in docs_svc.list_for_project(session, project_db_id):
        preamble = (d.preamble or "")[:120].replace("\n", " ")
        inventory_lines.append(f"- [{d.type.value}] {d.doc_key}: {d.title} — {preamble}")
    inventory = "\n".join(inventory_lines) or "(no documents)"

    prompt = (
        "You are advising a product analyst who is about to run AI story "
        "generation against this project's documentation. Assess the doc "
        "coverage and answer:\n"
        "1. Which doc types are strong / weak for story generation?\n"
        "2. What specific gaps would most improve generated stories?\n"
        "3. Which existing docs should be prioritised as context, beyond MASTER.md?\n\n"
        f"Doc inventory:\n{inventory}\n\n"
        "Return ONLY a valid JSON object (no markdown fences):\n"
        "{\n"
        '  "summary": "2-3 sentence assessment of overall coverage",\n'
        '  "strengths": ["short phrase", ...],\n'
        '  "gaps": ["doc_type — concrete description of what is missing", ...],\n'
        '  "recommendation": "1-2 sentence next-action suggestion"\n'
        "}\n"
        "Max 5 strengths, max 5 gaps. Same language as the doc titles."
    )

    coverage_path = proj.entry.cod_doc_dir / "context_coverage.json"
    try:
        raw = _call_lite_raw(prompt, cfg, max_tokens=1200).strip()
        if raw.startswith("```"):
            raw_lines = raw.splitlines()
            raw = "\n".join(raw_lines[1:-1] if raw_lines[-1].strip() == "```" else raw_lines[1:])
        data = json.loads(raw)
        coverage = {
            "summary": str(data.get("summary", "")),
            "strengths": [str(x) for x in data.get("strengths", [])][:5],
            "gaps": [str(x) for x in data.get("gaps", [])][:5],
            "recommendation": str(data.get("recommendation", "")),
            "generated_at": __import__("datetime")
            .datetime.now(__import__("datetime").UTC)
            .isoformat(),
        }
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2))
    except (AIBackendError, json.JSONDecodeError, Exception) as exc:
        coverage = {
            "summary": "",
            "strengths": [],
            "gaps": [],
            "recommendation": "",
            "generated_at": "",
            "error": str(exc),
        }

    return templates.TemplateResponse(
        request,
        "_frag/context_coverage.html",
        {"project": {"name": proj.entry.name}, "coverage": coverage},
    )


@router.post(
    "/p/{slug}/stories/section/{section_key}/analyze",
    response_class=HTMLResponse,
)
def stories_section_analyze(
    request: Request,
    slug: str,
    section_key: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """Run AI summary for a single section and persist it.

    Returns an HTML fragment (just the summary block) so HTMX can swap it
    into place inside the section header without re-rendering the whole grid.
    """
    from cod_doc.services import section_summary_service as summaries_svc

    proj = get_project(slug)
    session, project_db_id = db
    cfg = get_config()
    summary_path = proj.entry.cod_doc_dir / "section_summaries.json"

    # Collect every story whose parsed id_hint sits in the requested section.
    rows = stories.list_for_project(session, project_db_id)
    section_stories: dict[str, str] = {}
    for s in rows:
        parsed = _parse_narrative(s.narrative)
        if _section_of(parsed.get("id_hint", "") or "") == section_key:
            section_stories[s.story_id] = s.narrative

    if not section_stories:
        raise HTTPException(404, f"No stories in section {section_key}")

    try:
        summary = summaries_svc.generate(summary_path, section_key, section_stories, cfg)
    except AIBackendError as exc:
        return templates.TemplateResponse(
            request,
            "_frag/section_summary.html",
            {
                "project": {"name": proj.entry.name},
                "section": section_key,
                "summary": None,
                "error": str(exc),
            },
        )

    return templates.TemplateResponse(
        request,
        "_frag/section_summary.html",
        {
            "project": {"name": proj.entry.name},
            "section": section_key,
            "summary": {
                "text": summary.text,
                "generated_at": summary.generated_at,
                "stale": False,
            },
            "error": "",
        },
    )


@router.post(
    "/p/{slug}/stories/{story_id}/status",
    response_class=HTMLResponse,
)
def story_update_status(
    request: Request,
    slug: str,
    story_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    new_status: str = Form(...),
) -> Response:
    """Promote a story between statuses (draft → accepted, etc.)."""
    proj = get_project(slug)
    session, project_db_id = db
    story = stories.get(session, story_id)
    if story is None or story.project_id != project_db_id:
        raise HTTPException(404, f"Story не найдена: {story_id}")
    try:
        target = UserStoryStatus(new_status.strip().lower())
    except ValueError as exc:
        raise HTTPException(400, f"Unknown status: {new_status}") from exc
    stories.update_status(
        session,
        story_id=story_id,
        new_status=target,
        author="human:web",
        reason="status-promote",
    )
    session.commit()
    return RedirectResponse(url=f"/p/{proj.entry.name}/stories/{story_id}", status_code=303)


@router.post(
    "/p/{slug}/stories/{story_id}/tasks/generate",
    response_class=HTMLResponse,
)
def story_tasks_generate(
    request: Request,
    slug: str,
    story_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    plan_scope: str = Form(""),
    intent: str = Form(""),
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    cfg = get_config()
    story = stories.get(session, story_id)
    if story is None or story.project_id != project_db_id:
        raise HTTPException(404, f"Story не найдена: {story_id}")

    section_layout: list[tuple[str, str]] = []
    plan_id: int | None = None
    if plan_scope:
        plan = plans.get_by_scope(session, plan_scope.strip())
        if plan and plan.row_id is not None:
            plan_id = plan.row_id
            section_layout = [
                (s.letter.upper(), s.title) for s in plans.list_sections(session, plan.row_id)
            ]

    try:
        drafts, meta = ai_generate.generate_tasks_for_story(
            story.persona,
            story.narrative,
            cfg=cfg,
            section_layout=section_layout or None,
            intent=intent,
        )
        notice = f"{len(drafts)} tasks proposed — review and save."
        trace_service.record(
            session,
            model=meta.model,
            task_id=None,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            duration_ms=meta.duration_ms,
            tool_calls=[{"name": f"generate_tasks_for_story:{story_id}"}],
        )
        session.commit()
    except AIBackendError as exc:
        drafts = []
        notice = f"AI error: {exc}"
        trace_service.record(session, model=cfg.model, error=str(exc))
        session.commit()

    return templates.TemplateResponse(
        request,
        "_frag/task_drafts.html",
        {
            "project": {"name": proj.entry.name},
            "story": {"story_id": story.story_id},
            "plan_scope": plan_scope,
            "plan_id": plan_id,
            "section_layout": section_layout,
            "drafts": [
                {
                    "i": i,
                    "title": d.title,
                    "type": d.type,
                    "priority": d.priority,
                    "section_letter": d.section_letter,
                    "description": d.description or "",
                }
                for i, d in enumerate(drafts)
            ],
            "intent": intent,
            "notice": notice,
        },
    )


@router.post(
    "/p/{slug}/stories/{story_id}/tasks/save",
    response_class=HTMLResponse,
)
async def story_tasks_save(
    request: Request,
    slug: str,
    story_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    story = stories.get(session, story_id)
    if story is None or story.project_id != project_db_id:
        raise HTTPException(404, f"Story не найдена: {story_id}")
    assert story.row_id is not None

    form = await request.form()
    plan_scope = str(form.get("plan_scope") or "").strip()
    if not plan_scope:
        raise HTTPException(400, "plan_scope is required to save tasks")
    plan = plans.get_by_scope(session, plan_scope)
    if plan is None or plan.row_id is None:
        raise HTTPException(404, f"Plan '{plan_scope}' not found")

    sections = plans.list_sections(session, plan.row_id)
    section_by_letter = {s.letter.upper(): s for s in sections}

    selected = set(form.getlist("selected"))
    titles = form.getlist("title")
    types = form.getlist("type")
    priorities = form.getlist("priority")
    section_letters = form.getlist("section_letter")
    descriptions = form.getlist("description")

    saved = 0
    for i, title in enumerate(titles):
        if str(i) not in selected:
            continue
        type_ = types[i] if i < len(types) else "feature"
        priority = priorities[i] if i < len(priorities) else "medium"
        letter = str(section_letters[i] if i < len(section_letters) else "").upper().strip()
        description = str(descriptions[i] if i < len(descriptions) else "")
        section = section_by_letter.get(letter)
        if section is None or section.row_id is None:
            section = sections[0] if sections else None
        if section is None or section.row_id is None:
            continue
        # COD-071: task + link share one savepoint — either both land or
        # neither does, and the rest of the batch survives a single failure.
        try:
            with session.begin_nested():
                task = task_svc.create(
                    session,
                    project_id=project_db_id,
                    plan_id=plan.row_id,
                    section_id=section.row_id,
                    title=str(title).strip(),
                    type=TaskType(str(type_)),
                    priority=Priority(str(priority)),
                    author="human:web",
                    description=description.strip() or None,
                    id_prefix=_id_prefix_from_plan_scope(plan_scope),
                    allow_duplicate=True,
                    reason=f"story:{story_id}",
                )
                stories.link(
                    session,
                    story_id=story_id,
                    to_kind=StoryLinkKind.TASK,
                    to_ref=task.task_id,
                    relation=StoryRelation.IMPLEMENTED_BY,
                    author="human:web",
                    reason=f"ai-generate:{story_id}",
                )
        except Exception:
            continue
        saved += 1
    session.commit()
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/stories/{story_id}?saved={saved}",
        status_code=303,
    )
