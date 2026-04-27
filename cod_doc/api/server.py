"""
FastAPI REST API для production-режима COD-DOC.
Запуск: uvicorn cod_doc.api.server:app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cod_doc.agent.orchestrator import run_daemon
from cod_doc.config import Config
from cod_doc.logging_config import setup_logging

from cod_doc.api.deps import get_daemon_task, set_config, set_daemon_task
from cod_doc.api.routes import router as core_router
from cod_doc.api.web import router as web_router
from cod_doc.api.web.templates_env import STATIC_DIR
from cod_doc.api.webhooks import router as webhook_router

setup_logging()
logger = logging.getLogger("cod_doc.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = Config.load()
    set_config(cfg)
    logger.info(f"COD-DOC API запущен. Проектов: {len(cfg.list_projects())}")
    if cfg.is_configured:
        task = asyncio.create_task(
            run_daemon(cfg, log_callback=lambda m: logger.info(m))
        )
        set_daemon_task(task)
    yield
    daemon = get_daemon_task()
    if daemon:
        daemon.cancel()


app = FastAPI(
    title="COD-DOC API",
    description="Context Orchestrator for Documentation — REST API",
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(core_router)
app.include_router(webhook_router)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
