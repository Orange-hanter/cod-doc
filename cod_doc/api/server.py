"""
FastAPI REST API для production-режима COD-DOC.
Запуск: uvicorn cod_doc.api.server:app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cod_doc.agent.orchestrator import AgentEvent, Orchestrator, run_daemon
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project, Task

logger = logging.getLogger("cod_doc.api")

# ── Lifespan ──────────────────────────────────────────────────────────────────

_daemon_task: asyncio.Task | None = None
_config: Config | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _daemon_task, _config
    _config = Config.load()
    logger.info(f"COD-DOC API запущен. Проектов: {len(_config.list_projects())}")
    if _config.is_configured:
        _daemon_task = asyncio.create_task(
            run_daemon(_config, log_callback=lambda m: logger.info(m))
        )
    yield
    if _daemon_task:
        _daemon_task.cancel()


app = FastAPI(
    title="COD-DOC API",
    description="Context Orchestrator for Documentation — REST API",
    version="1.0.0",
    lifespan=lifespan,
)


def _get_config() -> Config:
    if _config is None:
        raise HTTPException(500, "Конфиг не загружен")
    return _config


def _get_project(name: str) -> Project:
    cfg = _get_config()
    entry = cfg.get_project(name)
    if not entry:
        raise HTTPException(404, f"Проект не найден: {name}")
    return Project(entry)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    path: str
    master_md: str = "MASTER.md"
    auto_commit: bool = False


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: int = 5
    context_refs: list[str] = []


class ConfigUpdate(BaseModel):
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    auto_commit: bool | None = None
    agent_interval: int | None = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    cfg = _get_config()
    return {"status": "ok", "configured": cfg.is_configured, "projects": len(cfg.list_projects())}


# Config
@app.get("/api/config")
def get_config() -> dict:
    cfg = _get_config()
    data = cfg.model_dump()
    data.pop("api_key", None)  # Не отдавать ключ
    return data


@app.patch("/api/config")
def update_config(update: ConfigUpdate) -> dict:
    cfg = _get_config()
    for field, value in update.model_dump(exclude_none=True).items():
        setattr(cfg, field, value)
    cfg.save()
    return {"updated": True}


# Projects
@app.get("/api/projects")
def list_projects() -> list[dict]:
    cfg = _get_config()
    result = []
    for entry in cfg.list_projects():
        proj = Project(entry)
        result.append({**entry.model_dump(), "stats": proj.stats()})
    return result


@app.post("/api/projects", status_code=201)
def create_project(data: ProjectCreate) -> dict:
    cfg = _get_config()
    entry = ProjectEntry(**data.model_dump())
    cfg.add_project(entry)
    proj = Project(entry)
    proj.init()
    return {"created": entry.name}


@app.delete("/api/projects/{name}")
def delete_project(name: str) -> dict:
    cfg = _get_config()
    if not cfg.remove_project(name):
        raise HTTPException(404, f"Проект не найден: {name}")
    return {"deleted": name}


@app.get("/api/projects/{name}")
def get_project(name: str) -> dict:
    proj = _get_project(name)
    return {
        **proj.entry.model_dump(),
        "stats": proj.stats(),
        "master_exists": proj.entry.master_path.exists(),
    }


@app.get("/api/projects/{name}/master")
def get_master(name: str) -> dict:
    proj = _get_project(name)
    content = proj.read_master()
    if content is None:
        raise HTTPException(404, "MASTER.md не найден")
    return {"content": content}


# Tasks
@app.get("/api/projects/{name}/tasks")
def list_tasks(name: str, status: str | None = None) -> list[dict]:
    from cod_doc.core.project import TaskStatus
    proj = _get_project(name)
    s = TaskStatus(status) if status else None
    return [t.to_dict() for t in proj.get_tasks(s)]


@app.post("/api/projects/{name}/tasks", status_code=201)
def create_task(name: str, data: TaskCreate) -> dict:
    proj = _get_project(name)
    task = Task(**data.model_dump())
    proj.add_task(task)
    return task.to_dict()


@app.patch("/api/projects/{name}/tasks/{task_id}")
def update_task(name: str, task_id: str, body: dict) -> dict:
    proj = _get_project(name)
    task = proj.update_task(task_id, **body)
    if not task:
        raise HTTPException(404, f"Задача не найдена: {task_id}")
    return task.to_dict()


# Agent run
@app.post("/api/projects/{name}/run")
async def run_agent(name: str, background_tasks: BackgroundTasks) -> dict:
    """Запустить агент в фоне для проекта."""
    proj = _get_project(name)
    cfg = _get_config()
    if not cfg.is_configured:
        raise HTTPException(400, "API-ключ не настроен")

    async def _run() -> None:
        orch = Orchestrator(proj, cfg)
        async for event in orch.run_autonomous():
            logger.info(f"[{name}] {event.type}: {event.data}")

    background_tasks.add_task(_run)
    return {"started": True, "project": name}


# WebSocket — стриминг агента
@app.websocket("/ws/projects/{name}/run")
async def ws_run_agent(websocket: WebSocket, name: str) -> None:
    """WebSocket для стриминга вывода агента в реальном времени."""
    await websocket.accept()
    try:
        proj = _get_project(name)
        cfg = _get_config()
        if not cfg.is_configured:
            await websocket.send_json({"type": "error", "data": "API-ключ не настроен"})
            return

        orch = Orchestrator(proj, cfg)
        async for event in orch.run_autonomous():
            await websocket.send_json(event.to_dict())
            await asyncio.sleep(0)  # yield

        await websocket.send_json({"type": "done", "data": "Завершено"})
    except WebSocketDisconnect:
        logger.info(f"WebSocket отключён: {name}")
    except HTTPException as e:
        await websocket.send_json({"type": "error", "data": e.detail})
    except Exception as e:
        await websocket.send_json({"type": "error", "data": str(e)})
    finally:
        await websocket.close()
