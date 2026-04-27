"""Web pages: server-rendered HTML."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.deps import get_config, get_project
from cod_doc.api.web.db_resolver import open_db_for_project
from cod_doc.api.web.templates_env import templates
from cod_doc.core.project import Project
from cod_doc.services import doc_service as docs

router = APIRouter()

MASTER_PREVIEW_LINES = 80


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    cfg = get_config()
    projects = []
    for entry in cfg.list_projects():
        projects.append(
            {
                "name": entry.name,
                "path": entry.path,
                "enabled": entry.enabled,
                "stats": Project(entry).stats(),
            }
        )
    return templates.TemplateResponse(
        request,
        "index.html",
        {"projects": projects, "configured": cfg.is_configured},
    )


@router.get("/p/{slug}", response_class=HTMLResponse)
def project_show(request: Request, slug: str) -> HTMLResponse:
    proj = get_project(slug)
    master = proj.read_master()
    master_preview, master_truncated = _preview(master, MASTER_PREVIEW_LINES)
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
            "stats": proj.stats(),
            "master_preview": master_preview,
            "master_truncated": master_truncated,
        },
    )


def _preview(text: str | None, max_lines: int) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    return "\n".join(lines[:max_lines]), True


@router.get("/p/{slug}/docs", response_class=HTMLResponse)
def docs_list(request: Request, slug: str) -> HTMLResponse:
    proj = get_project(slug)
    documents: list[dict] = []
    db_available = False
    with open_db_for_project(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            for d in docs.list_for_project(session, project_db_id):
                documents.append(
                    {
                        "doc_key": d.doc_key,
                        "title": d.title,
                        "type": d.type.value,
                        "status": d.status.value,
                        "owner": d.owner or "",
                        "last_updated": d.last_updated,
                    }
                )
    return templates.TemplateResponse(
        request,
        "project/docs_list.html",
        {
            "project": {"name": proj.entry.name},
            "documents": documents,
            "db_available": db_available,
        },
    )


@router.get("/p/{slug}/docs/{doc_key:path}", response_class=HTMLResponse)
def doc_show(request: Request, slug: str, doc_key: str) -> HTMLResponse:
    proj = get_project(slug)
    with open_db_for_project(slug) as (session, project_db_id):
        if session is None or project_db_id is None:
            raise HTTPException(404, f"DB-проект ещё не инициализирован: {slug}")
        doc = docs.get(session, project_db_id, doc_key)
        if doc is None or doc.row_id is None:
            raise HTTPException(404, f"Документ не найден: {doc_key}")
        sections = docs.get_sections(session, doc.row_id)
        body = docs.render_body(session, doc.row_id) or doc.preamble or ""
        return templates.TemplateResponse(
            request,
            "project/doc_show.html",
            {
                "project": {"name": proj.entry.name},
                "doc": {
                    "doc_key": doc.doc_key,
                    "path": doc.path,
                    "title": doc.title,
                    "type": doc.type.value,
                    "status": doc.status.value,
                    "owner": doc.owner or "",
                    "last_updated": doc.last_updated,
                },
                "sections": [
                    {"anchor": s.anchor, "heading": s.heading, "level": s.level}
                    for s in sections
                ],
                "body": body,
            },
        )
