"""Stories list, detail, AI-generation flow (COD-068, COD-069)."""

from __future__ import annotations

from typing import Annotated

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
from cod_doc.services import ai_generate
from cod_doc.services import plan_service as plans
from cod_doc.services import story_service as stories
from cod_doc.services import task_service as task_svc
from cod_doc.services import trace_service
from cod_doc.services.ai_text import AIBackendError

router = APIRouter()


def _gather_master_text(slug: str) -> str:
    """Read the project's MASTER.md as docs-source for story generation."""
    proj = get_project(slug)
    if proj.entry.master_path.exists():
        return proj.entry.master_path.read_text(encoding="utf-8", errors="replace")
    return ""


def _id_prefix_from_plan_scope(scope: str) -> str:
    """Derive a 2-5 caps prefix from a plan scope like 'cod-doc' → 'COD'."""
    letters = [c for c in scope.upper() if c.isalpha()]
    return "".join(letters[:3]) or "TSK"


# ── Stories list ───────────────────────────────────────────────────────


@router.get("/p/{slug}/stories", response_class=HTMLResponse)
def stories_list(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    proj = get_project(slug)
    session, project_db_id = db
    rows = stories.list_for_project(session, project_db_id)
    return templates.TemplateResponse(
        request,
        "project/stories_list.html",
        {
            "project": {"name": proj.entry.name},
            "stories": [
                {
                    "story_id": s.story_id,
                    "persona": s.persona,
                    "narrative": s.narrative,
                    "status": s.status.value,
                    "priority": s.priority.value,
                }
                for s in rows
            ],
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
        story_id = stories.next_story_id(session, project_db_id)
        try:
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
            saved += 1
        except Exception:
            session.rollback()
            continue
    session.commit()
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/stories?saved={saved}", status_code=303
    )


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
                {"position": a.position, "criterion": a.criterion, "met": a.met}
                for a in acceptance
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
                (s.letter.upper(), s.title)
                for s in plans.list_sections(session, plan.row_id)
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
        letter = (
            str(section_letters[i] if i < len(section_letters) else "").upper().strip()
        )
        description = str(descriptions[i] if i < len(descriptions) else "")
        section = section_by_letter.get(letter)
        if section is None or section.row_id is None:
            section = sections[0] if sections else None
        if section is None or section.row_id is None:
            continue
        try:
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
        except Exception:
            session.rollback()
            continue
        try:
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
            session.rollback()
        saved += 1
    session.commit()
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/stories/{story_id}?saved={saved}",
        status_code=303,
    )
