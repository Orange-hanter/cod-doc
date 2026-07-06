"""Project overview + DB-init handler + AI MASTER.md import (COD-060)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_config, get_project, get_project_db, try_open_project_db
from cod_doc.api.web.errors import truncate_for_cookie
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import EntityKind, Priority, TaskType
from cod_doc.services import ai_generate, trace_service
from cod_doc.services import plan_service as plans
from cod_doc.services import project_service as projects
from cod_doc.services import revision_service as revisions
from cod_doc.services import task_service as task_svc
from cod_doc.services.ai_text import AIBackendError

from ._helpers import MASTER_PREVIEW_LINES, _preview

router = APIRouter()

OVERVIEW_READY_LIMIT = 5
OVERVIEW_REVISIONS_LIMIT = 5


@router.get("/p/{slug}", response_class=HTMLResponse)
def project_show(request: Request, slug: str) -> HTMLResponse:
    proj = get_project(slug)
    master = proj.read_master()
    master_preview, master_truncated = _preview(master, MASTER_PREVIEW_LINES)
    master_html = render_markdown(master_preview or "") or None

    # WEB-014 — overview aggregator: ready-to-start tasks, plan-progress
    # mini-bars, recent revisions. Each block is independent and is left
    # empty (not crashed) if the DB project isn't initialised yet.
    ready_tasks: list[dict[str, Any]] = []
    plan_rows: list[dict[str, Any]] = []
    recent_revs: list[dict[str, Any]] = []
    db_available = False

    # Header KPI cards: prefer DB-aggregated totals (single source of truth
    # with the Plan-progress block below). Fall back to legacy YAML stats
    # when the DB isn't initialised — same shape so the template doesn't
    # need to branch.
    yaml_stats: dict[str, Any] = proj.stats()
    db_total = db_done = db_in_progress = db_blocked = 0

    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            for t in task_svc.list_for_project(session, project_db_id):
                if t.status.value == "blocked":
                    db_blocked += 1

            project_plans = plans.list_for_project(session, project_db_id)
            # COD-075: aggregate progress for every plan in one SQL — was N+1.
            progress_by_plan = plans.recalc_for_project(session, project_db_id)
            for plan in project_plans:
                assert plan.row_id is not None
                progress = progress_by_plan.get(plan.row_id)
                if progress is None:
                    # Plan exists but plan_totals view has no row (zero tasks).
                    continue
                db_total += progress.total
                db_done += progress.done
                db_in_progress += progress.in_progress
                plan_rows.append(
                    {
                        "plan_id": plan.row_id,
                        "scope": plan.scope,
                        "total": progress.total,
                        "done": progress.done,
                        "in_progress": progress.in_progress,
                        "remaining": progress.remaining,
                        "status": progress.status.value,
                        "percent": (
                            round(100 * progress.done / progress.total)
                            if progress.total
                            else 0
                        ),
                    }
                )

            # COD-075: ready batch across all plans in one SQL — was N+1.
            for t in plans.ready_for_project(
                session, project_db_id, limit=OVERVIEW_READY_LIMIT
            ):
                ready_tasks.append(
                    {
                        "task_id": t.task_id,
                        "title": t.title,
                        "type": t.type.value,
                        "status": t.status.value,
                        "priority": t.priority.value,
                        "plan_id": t.plan_id,
                        "section_id": t.section_id,
                    }
                )

            for r in revisions.list_recent_for_project(
                session, project_db_id, limit=OVERVIEW_REVISIONS_LIMIT
            ):
                recent_revs.append(
                    {
                        "revision_id": r.revision_id,
                        "entity_kind": r.entity_kind.value
                        if isinstance(r.entity_kind, EntityKind)
                        else str(r.entity_kind),
                        "entity_id": r.entity_id,
                        "author": r.author,
                        "at": r.at,
                        "reason": r.reason or "",
                    }
                )

    if db_available and any((db_total, db_done, db_in_progress)):
        kpi = {
            **yaml_stats,
            "total": db_total,
            "done": db_done,
            "in_progress": db_in_progress,
            "blocked": db_blocked,
            "pending": db_total - db_done - db_in_progress,
            "pct_done": round(100 * db_done / db_total) if db_total else 0,
        }
    else:
        kpi = {**yaml_stats, "blocked": 0, "pct_done": 0}

    return templates.TemplateResponse(
        request,
        "project/show.html",
        {
            "project": {
                "name": proj.entry.name,
                "path": proj.entry.path,
                "enabled": proj.entry.enabled,
                "master_md": proj.entry.master_md,
                "master_exists": proj.entry.master_path.exists(),
            },
            "stats": kpi,
            "master_preview": master_preview,
            "master_html": master_html,
            "master_truncated": master_truncated,
            "db_available": db_available,
            "ready_tasks": ready_tasks,
            "plan_rows": plan_rows,
            "recent_revs": recent_revs,
        },
    )


@router.post("/p/{slug}/init", response_class=HTMLResponse)
def project_init_db(request: Request, slug: str) -> Response:
    """Bootstrap the project's `.cod-doc/state.db` (alembic + ProjectModel row).

    Idempotent — calling on an already-initialised project is a no-op aside
    from a confirmation flash. After init, the engine cache is invalidated
    so subsequent reads see the freshly-migrated schema immediately.
    """
    proj = get_project(slug)
    result = projects.init_project(proj.entry)

    # Engine cache invalidation: drop the (slug → engine) cache so the next
    # request reopens against the migrated DB.
    from cod_doc.api.deps import dispose_all_engines

    dispose_all_engines()

    if result.db_row_existed:
        message = f"Проект «{slug}»: БД уже была инициализирована."
        severity = "info"
    elif result.db_existed:
        message = f"Проект «{slug}»: миграции применены, project-row создан."
        severity = "info"
    else:
        message = f"Проект «{slug}» инициализирован. Миграции применены, project-row создан."
        severity = "info"

    redirect = RedirectResponse(url=f"/p/{slug}", status_code=303)
    redirect.set_cookie("flash_severity", severity, max_age=30, path="/")
    redirect.set_cookie(
        "flash_message",
        quote(truncate_for_cookie(message)),
        max_age=30,
        path="/",
    )
    return redirect


# ── COD-060: AI-driven Import from folder ────────────────────────────────


_DOC_EXTS = {".md", ".rst", ".txt"}
_IMPORT_SCAN_LIMIT = 40
_IMPORT_FILE_BYTES = 4000
_IMPORT_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".cod-doc", "dist", "build"}


def _walk_doc_files(root: Path) -> list[tuple[str, str]]:
    """Walk repo_root for .md/.rst/.txt files. Skip vendor/build dirs."""
    files: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if len(files) >= _IMPORT_SCAN_LIMIT:
            break
        if not path.is_file() or path.suffix.lower() not in _DOC_EXTS:
            continue
        # COD-077: skip dotfiles (.env, .gitignore, .DS_Store.md…) — these
        # are project metadata, not documentation.
        if path.name.startswith("."):
            continue
        rel_parts = path.relative_to(root).parts
        if any(p in _IMPORT_SKIP_DIRS or p.startswith(".") for p in rel_parts[:-1]):
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")[:_IMPORT_FILE_BYTES]
        except OSError:
            continue
        files.append((str(path.relative_to(root)), body))
    return files


@router.post("/p/{slug}/import_master/scan", response_class=HTMLResponse)
def import_master_scan(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    intent: str = Form(""),
) -> Response:
    """Scan the repo, run AI, return a preview of MASTER.md + coverage tasks."""
    proj = get_project(slug)
    session, _project_db_id = db
    cfg = get_config()
    files = _walk_doc_files(Path(proj.entry.path))

    try:
        draft, meta = ai_generate.generate_master_from_folder(
            files, cfg=cfg, intent=intent
        )
        notice = (
            f"Scanned {len(draft.files_seen)} files; "
            f"{len(draft.coverage_tasks)} coverage tasks proposed."
        )
        trace_service.record(
            session,
            model=meta.model,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            duration_ms=meta.duration_ms,
            tool_calls=[{"name": "generate_master_from_folder"}],
        )
        session.commit()
    except AIBackendError as exc:
        draft = ai_generate.MasterDraft(
            master_md="", coverage_tasks=[], files_seen=[p for p, _ in files]
        )
        notice = f"AI error: {exc}"
        trace_service.record(session, model=cfg.model, error=str(exc))
        session.commit()

    return templates.TemplateResponse(
        request,
        "_frag/master_draft.html",
        {
            "project": {"name": proj.entry.name},
            "files_seen": draft.files_seen,
            "master_md": draft.master_md,
            "coverage_tasks": [
                {
                    "i": i,
                    "title": t.title,
                    "type": t.type,
                    "priority": t.priority,
                    "section_letter": t.section_letter,
                    "description": t.description or "",
                }
                for i, t in enumerate(draft.coverage_tasks)
            ],
            "notice": notice,
            "intent": intent,
        },
    )


@router.post("/p/{slug}/import_master/save", response_class=HTMLResponse)
async def import_master_save(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """Write MASTER.md and create selected coverage tasks under a plan section."""
    proj = get_project(slug)
    session, project_db_id = db

    form = await request.form()
    master_md = str(form.get("master_md") or "")
    if not master_md.strip():
        raise HTTPException(400, "master_md is empty")

    write_master = form.get("write_master") == "on"
    if write_master:
        proj.entry.master_path.parent.mkdir(parents=True, exist_ok=True)
        proj.entry.master_path.write_text(master_md, encoding="utf-8")

    plan_scope = str(form.get("plan_scope") or "").strip()
    saved_tasks = 0
    if plan_scope:
        plan = plans.get_by_scope(session, plan_scope)
        if plan is None or plan.row_id is None:
            raise HTTPException(404, f"Plan '{plan_scope}' not found")
        sections = plans.list_sections(session, plan.row_id)
        if not sections:
            raise HTTPException(400, f"Plan '{plan_scope}' has no sections")
        section_by_letter = {s.letter.upper(): s for s in sections}

        selected = set(form.getlist("selected"))
        titles = form.getlist("title")
        types = form.getlist("type")
        priorities = form.getlist("priority")
        section_letters = form.getlist("section_letter")
        descriptions = form.getlist("description")

        for i, title in enumerate(titles):
            if str(i) not in selected:
                continue
            type_ = types[i] if i < len(types) else "docs"
            priority = priorities[i] if i < len(priorities) else "medium"
            letter = (
                str(section_letters[i] if i < len(section_letters) else "").upper().strip()
            )
            description = str(descriptions[i] if i < len(descriptions) else "")
            section = section_by_letter.get(letter) or sections[0]
            if section.row_id is None:
                continue
            # COD-071: savepoint per row so one duplicate-title rejection
            # doesn't roll back the rest of the batch.
            try:
                with session.begin_nested():
                    task_svc.create(
                        session,
                        project_id=project_db_id,
                        plan_id=plan.row_id,
                        section_id=section.row_id,
                        title=str(title).strip(),
                        type=TaskType(str(type_)),
                        priority=Priority(str(priority)),
                        author="human:web",
                        description=description.strip() or None,
                        id_prefix=_id_prefix_from_scope(plan_scope),
                        allow_duplicate=True,
                        reason="ai-import-master",
                    )
            except Exception:
                continue
            saved_tasks += 1
    session.commit()

    return RedirectResponse(
        url=f"/p/{slug}?master_written={'1' if write_master else '0'}"
        f"&tasks_saved={saved_tasks}",
        status_code=303,
    )


def _id_prefix_from_scope(scope: str) -> str:
    letters = [c for c in scope.upper() if c.isalpha()]
    return "".join(letters[:3]) or "TSK"
