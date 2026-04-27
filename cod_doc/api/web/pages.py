"""Web pages: server-rendered HTML."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.deps import get_config, get_project
from cod_doc.api.web.templates_env import templates
from cod_doc.core.project import Project

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
