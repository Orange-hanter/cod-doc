"""Standards catalog browser — `/standards`.

Renders the shipped skill catalog (``cod_doc/skills/``) so users can see
which default standards / conventions are baked into every project.  Each
skill is shown with its trigger description and (on demand) its full body.

These skills are package-shipped — read-only here.  Editing is done by
modifying the SKILL.md files in source.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.services import skill_service

router = APIRouter()


@router.get("/standards", response_class=HTMLResponse)
def standards_list(request: Request) -> HTMLResponse:
    """List every shipped skill / standard with its trigger description."""
    records = skill_service.list_skills()

    skills = []
    for r in records:
        name = r.get("name", "")
        body = skill_service.get_skill_body(name) or ""
        # Description in frontmatter is often a YAML block scalar — strip
        # the trailing "Триггеры: …" hint to keep the summary card tight.
        desc = (r.get("description") or "").strip()
        # First sentence/paragraph is usually the "what" — keep that as
        # subtitle, push the rest into the expanded body.
        if "\n" in desc:
            short_desc = desc.split("\n", 1)[0].strip()
            rest_desc = desc.split("\n", 1)[1].strip()
        else:
            short_desc = desc
            rest_desc = ""

        skills.append(
            {
                "name": name,
                "short_description": short_desc,
                "rest_description": rest_desc,
                "body_html": render_markdown(body) if body else "",
                "is_base": name == "orchestrator",
            }
        )

    return templates.TemplateResponse(
        request,
        "standards/list.html",
        {"skills": skills},
    )


@router.get("/standards/{name}", response_class=HTMLResponse)
def standard_show(request: Request, name: str) -> HTMLResponse:
    """Detail page for one skill — useful for deep-linking from agent traces."""
    record = skill_service.get_skill(name)
    if record is None:
        raise HTTPException(404, f"Standard '{name}' not found")

    body = skill_service.get_skill_body(name) or ""
    return templates.TemplateResponse(
        request,
        "standards/show.html",
        {
            "skill": {
                "name": name,
                "description": record.get("description") or "",
                "body_html": render_markdown(body) if body else "",
                "is_base": name == "orchestrator",
            },
        },
    )
