"""Global config — view + save."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cod_doc.api.deps import get_config
from cod_doc.api.web.templates_env import templates
from cod_doc.core.embeddings.registry import list_embedding_adapters
from cod_doc.core.embeddings.settings import DEFAULT_BATCH_SIZE
from cod_doc.services import model_catalog

from ._helpers import _masked_api_key, ensure_loopback_client

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
                "agent_enabled": cfg.agent_enabled,
                "max_iterations": cfg.max_iterations,
                "agent_interval": cfg.agent_interval,
                "embedding_model": cfg.embedding_model,
                "embedding_backend": cfg.embedding_backend,
                "embedding_api_key_masked": _masked_api_key(cfg.embedding_api_key),
                "embedding_api_key_set": bool(cfg.embedding_api_key),
                "embedding_base_url": cfg.embedding_base_url,
                "embedding_dimensions": cfg.embedding_dimensions or "",
                "embedding_batch_size": cfg.embedding_batch_size,
                "lite_model": cfg.lite_model,
                "doc_max_tokens_heavy": cfg.doc_max_tokens_heavy,
                "doc_max_tokens_default": cfg.doc_max_tokens_default,
            },
            "model_catalog": [
                {
                    "model_id": m.model_id,
                    "label": m.label,
                    "describe": m.describe(),
                    "notes": m.notes,
                }
                for m in model_catalog.CATALOG
            ],
            "model_is_custom": not model_catalog.is_known(cfg.model),
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
    agent_enabled: str = Form(""),  # checkbox: "on" or absent
    max_iterations: int = Form(...),
    agent_interval: int = Form(...),
    embedding_model: str = Form(...),
    embedding_backend: str = Form("openai"),
    embedding_api_key: str = Form(""),
    embedding_base_url: str = Form(""),
    embedding_dimensions: str = Form(""),
    embedding_batch_size: int = Form(DEFAULT_BATCH_SIZE),
    lite_model: str = Form(""),
    doc_max_tokens_heavy: int = Form(64000),
    doc_max_tokens_default: int = Form(16000),
) -> Response:
    ensure_loopback_client(request)  # SYM-003: endpoint пишет LLM-ключ
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
    cfg.agent_enabled = agent_enabled == "on"
    cfg.max_iterations = max_iterations
    cfg.agent_interval = agent_interval
    cfg.embedding_model = embedding_model.strip()
    backend = embedding_backend.strip().lower()
    # Нормализуем до save(): валидатор Config поднимает ValueError, а форма
    # не должна ронять сервер из-за подделанного значения.
    if backend not in list_embedding_adapters():
        backend = "openai"
    cfg.embedding_backend = backend
    # ADO-071: второй секрет живёт по тем же правилам, что api_key —
    # пусто = не трогать, "-" = удалить.
    if embedding_api_key == "-":
        cfg.embedding_api_key = ""
    elif embedding_api_key.strip():
        cfg.embedding_api_key = embedding_api_key.strip()
    cfg.embedding_base_url = embedding_base_url.strip()
    dimensions = embedding_dimensions.strip()
    cfg.embedding_dimensions = int(dimensions) if dimensions.isdigit() else None
    cfg.embedding_batch_size = max(1, embedding_batch_size)
    cfg.lite_model = lite_model.strip()
    cfg.doc_max_tokens_heavy = max(1024, doc_max_tokens_heavy)
    cfg.doc_max_tokens_default = max(1024, doc_max_tokens_default)
    cfg.save()
    return RedirectResponse(url="/settings", status_code=303)
