"""
FastAPI REST API для production-режима COD-DOC.
Запуск: uvicorn cod_doc.api.server:app --host 127.0.0.1 --port 8765
(SYM-003: loopback по умолчанию; 0.0.0.0 — только осознанно, COD_DOC_BIND)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from cod_doc.api.deps import (
    dispose_all_engines,
    set_config,
    start_daemon,
    stop_daemon,
)
from cod_doc.api.routes import router as core_router
from cod_doc.api.v1.routes import router as v1_router
from cod_doc.api.web import fragments_router, pages_router
from cod_doc.api.web.errors import WebError, truncate_for_cookie
from cod_doc.api.web.templates_env import STATIC_DIR, templates
from cod_doc.api.webhooks import router as webhook_router
from cod_doc.api.websocket import router as websocket_router
from cod_doc.config import Config
from cod_doc.logging_config import setup_logging
from cod_doc.services import event_bus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

setup_logging()
logger = logging.getLogger("cod_doc.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg = Config.load()
    set_config(cfg)
    projects = cfg.list_projects()
    logger.info(f"COD-DOC API запущен. Проектов: {len(projects)}")
    if cfg.is_configured and cfg.agent_enabled:
        started = start_daemon(log_callback=lambda m: logger.info(m))
        if not started:
            logger.warning("Daemon не запущен (agent_enabled=False или нет api_key)")
    else:
        logger.info("Автономный агент отключён (agent_enabled=False)")
    yield
    stop_daemon()
    dispose_all_engines()
    event_bus.dispose()  # STB-022: clear WebSocket subscriber registry on shutdown


app = FastAPI(
    title="COD-DOC API",
    description="Context Orchestrator for Documentation — REST API",
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(core_router)
app.include_router(v1_router)  # SYM-006C: /api/v1 — единственная поверхность будущего Bearer-гейта
app.include_router(webhook_router)
app.include_router(websocket_router)
app.include_router(fragments_router)
app.include_router(pages_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(WebError)
async def web_error_handler(request: Request, exc: WebError) -> Response:
    """Render WebError as alert fragment (HTMX) or cookie-flash + redirect (form)."""
    # Operational visibility: every web-error event lands in the same logger
    # as the rest of the API surface. INFO level — these are user-facing
    # 4xx, not server bugs.
    logger.info(
        "WebError %s %d severity=%s msg=%s",
        request.url.path,
        exc.status_code,
        exc.severity,
        exc.message,
    )
    htmx = request.headers.get("HX-Request", "").lower() == "true"
    if htmx:
        alert_html = templates.get_template("_frag/alert.html").render(
            severity=exc.severity, message=exc.message, oob=True
        )
        # HX-Reswap: none — suppress main-target swap so only the OOB #alerts
        # update lands. The handler that raised this WebError didn't render
        # any meaningful main-target HTML.
        return HTMLResponse(
            alert_html,
            status_code=exc.status_code,
            headers={"HX-Reswap": "none"},
        )
    referer = request.headers.get("Referer") or "/"
    response = RedirectResponse(url=referer, status_code=303)
    # Plain cookies; flash is short-lived and not security-sensitive (the
    # message comes from the same trust domain). Path "/" so any page reads it.
    # Cookie headers are latin-1 only, so percent-encode the message;
    # truncate first to stay under browser's 4 KB cookie cap.
    response.set_cookie("flash_severity", exc.severity, max_age=30, path="/")
    response.set_cookie(
        "flash_message",
        quote(truncate_for_cookie(exc.message)),
        max_age=30,
        path="/",
    )
    return response
