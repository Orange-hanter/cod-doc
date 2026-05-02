"""Global config — view + save."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cod_doc.api.deps import get_config
from cod_doc.api.web.templates_env import templates

from ._helpers import _masked_api_key

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_show(request: Request) -> HTMLResponse:
    cfg = get_config()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "config": {
                "api_key_masked": _masked_api_key(cfg.api_key),
                "api_key_set": bool(cfg.api_key),
                "base_url": cfg.base_url,
                "model": cfg.model,
                "max_tokens": cfg.max_tokens,
                "auto_commit": cfg.auto_commit,
                "max_iterations": cfg.max_iterations,
                "agent_interval": cfg.agent_interval,
                "embedding_model": cfg.embedding_model,
            },
        },
    )


@router.post("/settings", response_class=HTMLResponse)
def settings_save(
    request: Request,
    api_key: str = Form(""),
    base_url: str = Form(...),
    model: str = Form(...),
    max_tokens: int = Form(...),
    auto_commit: str = Form(""),  # checkbox: "on" or absent
    max_iterations: int = Form(...),
    agent_interval: int = Form(...),
    embedding_model: str = Form(...),
) -> Response:
    cfg = get_config()
    # Empty api_key on POST means "leave existing untouched" — typical web UX
    # for password/secret fields. Forces an explicit "delete" by typing the
    # plain literal "-" (documented next to the field).
    if api_key == "-":
        cfg.api_key = ""
    elif api_key.strip():
        cfg.api_key = api_key.strip()
    cfg.base_url = base_url.strip()
    cfg.model = model.strip()
    cfg.max_tokens = max_tokens
    cfg.auto_commit = auto_commit == "on"
    cfg.max_iterations = max_iterations
    cfg.agent_interval = agent_interval
    cfg.embedding_model = embedding_model.strip()
    cfg.save()
    return RedirectResponse(url="/settings", status_code=303)
