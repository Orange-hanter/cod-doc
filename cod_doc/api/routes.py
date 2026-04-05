"""REST-маршруты для проектов, задач, конфигурации."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect

from cod_doc.agent.orchestrator import Orchestrator
from cod_doc.config import ProjectEntry
from cod_doc.core.project import Project, Task, TaskStatus

from cod_doc.api.deps import get_config, get_project
from cod_doc.api.schemas import ConfigUpdate, ProjectCreate, TaskCreate

logger = logging.getLogger("cod_doc.api")

router = APIRouter(prefix="/api")


# ── Health / Config ───────────────────────────────────────────────────────────

@router.get("/health")
def health() -> dict:
    cfg = get_config()
    return {"status": "ok", "configured": cfg.is_configured, "projects": len(cfg.list_projects())}


@router.get("/config")
def read_config() -> dict:
    cfg = get_config()
    data = cfg.model_dump()
    data.pop("api_key", None)
    return data


@router.patch("/config")
def update_config(update: ConfigUpdate) -> dict:
    cfg = get_config()
    for field, value in update.model_dump(exclude_none=True).items():
        setattr(cfg, field, value)
    cfg.save()
    return {"updated": True}


# ── Projects ──────────────────────────────────────────────────────────────────

@router.get("/projects")
def list_projects() -> list[dict]:
    cfg = get_config()
    result = []
    for entry in cfg.list_projects():
        proj = Project(entry)
        result.append({**entry.model_dump(), "stats": proj.stats()})
    return result


@router.post("/projects", status_code=201)
def create_project(data: ProjectCreate) -> dict:
    cfg = get_config()
    entry = ProjectEntry(**data.model_dump())
    cfg.add_project(entry)
    proj = Project(entry)
    proj.init()
    return {"created": entry.name}


@router.delete("/projects/{name}")
def delete_project(name: str) -> dict:
    cfg = get_config()
    if not cfg.remove_project(name):
        raise HTTPException(404, f"Проект не найден: {name}")
    return {"deleted": name}


@router.get("/projects/{name}")
def read_project(name: str) -> dict:
    proj = get_project(name)
    return {
        **proj.entry.model_dump(),
        "stats": proj.stats(),
        "master_exists": proj.entry.master_path.exists(),
    }


@router.get("/projects/{name}/master")
def read_master(name: str) -> dict:
    proj = get_project(name)
    content = proj.read_master()
    if content is None:
        raise HTTPException(404, "MASTER.md не найден")
    return {"content": content}


# ── Tasks ─────────────────────────────────────────────────────────────────────

@router.get("/projects/{name}/tasks")
def list_tasks(name: str, status: str | None = None) -> list[dict]:
    proj = get_project(name)
    s = TaskStatus(status) if status else None
    return [t.to_dict() for t in proj.get_tasks(s)]


@router.post("/projects/{name}/tasks", status_code=201)
def create_task(name: str, data: TaskCreate) -> dict:
    proj = get_project(name)
    task = Task(**data.model_dump())
    proj.add_task(task)
    return task.to_dict()


@router.patch("/projects/{name}/tasks/{task_id}")
def update_task(name: str, task_id: str, body: dict) -> dict:
    proj = get_project(name)
    task = proj.update_task(task_id, **body)
    if not task:
        raise HTTPException(404, f"Задача не найдена: {task_id}")
    return task.to_dict()


# ── Agent ─────────────────────────────────────────────────────────────────────

@router.post("/projects/{name}/run")
async def run_agent(name: str, background_tasks: BackgroundTasks) -> dict:
    """Запустить агент в фоне для проекта."""
    proj = get_project(name)
    cfg = get_config()
    if not cfg.is_configured:
        raise HTTPException(400, "API-ключ не настроен")

    async def _run() -> None:
        orch = Orchestrator(proj, cfg)
        async for event in orch.run_autonomous():
            logger.info(f"[{name}] {event.type}: {event.data}")

    background_tasks.add_task(_run)
    return {"started": True, "project": name}
