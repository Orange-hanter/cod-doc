"""Общие зависимости и хелперы API."""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project

logger = logging.getLogger("cod_doc.api")

# Runtime-состояние (задаётся в lifespan)
_daemon_task: asyncio.Task | None = None
_config: Config | None = None
webhook_registry: dict[str, dict] = {}


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg


def set_daemon_task(task: asyncio.Task | None) -> None:
    global _daemon_task
    _daemon_task = task


def get_daemon_task() -> asyncio.Task | None:
    return _daemon_task


def get_config() -> Config:
    if _config is None:
        raise HTTPException(500, "Конфиг не загружен")
    return _config


def get_project(name: str) -> Project:
    cfg = get_config()
    entry = cfg.get_project(name)
    if not entry:
        raise HTTPException(404, f"Проект не найден: {name}")
    return Project(entry)
