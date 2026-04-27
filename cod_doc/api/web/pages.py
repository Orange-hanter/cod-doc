"""Web pages: server-rendered HTML."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.deps import get_config
from cod_doc.api.web.templates_env import templates
from cod_doc.core.project import Project

router = APIRouter()


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
