"""REST-маршруты для проектов, задач, конфигурации."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from cod_doc.agent.orchestrator import Orchestrator
from cod_doc.api import legacy_tasks
from cod_doc.api.deps import (
    daemon_is_running,
    ensure_loopback_client,
    get_config,
    get_engine_for_slug,
    get_project,
    start_daemon,
    stop_daemon,
)
from cod_doc.api.schemas import ConfigUpdate, ProjectCreate, TaskCreate
from cod_doc.config import SECRET_FIELDS, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import project_health_service, task_service

logger = logging.getLogger("cod_doc.api")

router = APIRouter(prefix="/api")


# ── Health / Config ───────────────────────────────────────────────────────────


@router.get("/health")
def health() -> dict[str, Any]:
    cfg = get_config()
    return {"status": "ok", "configured": cfg.is_configured, "projects": len(cfg.list_projects())}


@router.get("/config")
def read_config() -> dict[str, Any]:
    cfg = get_config()
    data = cfg.model_dump()
    # ADO-096: секретов теперь несколько (LLM, Anthropic, эмбеддер) — вырезаем
    # по единому списку, чтобы следующий ключ не утёк по недосмотру.
    for field in SECRET_FIELDS:
        data.pop(field, None)
    return data


@router.patch("/config")
def update_config(update: ConfigUpdate, request: Request) -> dict[str, Any]:
    ensure_loopback_client(request)  # ADO-035: endpoint пишет LLM-ключ
    cfg = get_config()
    for field, value in update.model_dump(exclude_none=True).items():
        setattr(cfg, field, value)
    cfg.save()
    return {"updated": True}


# ── Projects ──────────────────────────────────────────────────────────────────


@router.get("/projects")
def list_projects() -> list[dict[str, Any]]:
    cfg = get_config()
    result = []
    for entry in cfg.list_projects():
        proj = Project(entry)
        result.append({**entry.model_dump(), "stats": proj.stats()})
    return result


@router.post("/projects", status_code=201)
def create_project(data: ProjectCreate) -> dict[str, Any]:
    cfg = get_config()
    entry = ProjectEntry(**data.model_dump())
    cfg.add_project(entry)
    proj = Project(entry)
    proj.init()
    return {"created": entry.name}


@router.delete("/projects/{name}")
def delete_project(name: str) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.remove_project(name):
        raise HTTPException(404, f"Проект не найден: {name}")
    return {"deleted": name}


@router.get("/projects/{name}")
def read_project(name: str) -> dict[str, Any]:
    proj = get_project(name)
    return {
        **proj.entry.model_dump(),
        "stats": proj.stats(),
        "master_exists": proj.entry.master_path.exists(),
    }


@router.get("/projects/{name}/master")
def read_master(name: str) -> dict[str, Any]:
    proj = get_project(name)
    content = proj.read_master()
    if content is None:
        raise HTTPException(404, "MASTER.md не найден")
    return {"content": content}


@router.get("/projects/{name}/health")
def read_project_health(name: str) -> dict[str, Any]:
    """Read-only DB health summary for automation and dashboards."""
    proj = get_project(name)
    engine = get_engine_for_slug(name)
    if engine is None:
        return {"project": name, **project_health_service.uninitialized_project_health()}

    factory = make_session_factory(engine)
    session = factory()
    try:
        db_project = ProjectRepository(session).get_by_slug(name)
        if db_project is None or db_project.row_id is None:
            return {"project": name, **project_health_service.uninitialized_project_health()}
        return {
            "project": name,
            **project_health_service.build_project_health(
                session,
                db_project.row_id,
                root_path=Path(proj.entry.path),
            ),
        }
    finally:
        session.close()


# ── Tasks ─────────────────────────────────────────────────────────────────────
# ADO-037: legacy tasks-эндпоинты переведены с YAML-пути (core/project.py)
# на task_service — Revision + activity event + статус-машина. Проект без
# инициализированной state.db получает 409.

_SUPPORTED_PATCH_FIELDS = frozenset({"status", "result"})


def _legacy_session(name: str) -> tuple[Any, int]:
    """(session, project_id) для DB-backed legacy-эндпоинтов или HTTP-ошибка."""
    engine = get_engine_for_slug(name)
    if engine is None:
        raise HTTPException(
            409,
            f"Проект '{name}' не инициализирован в БД "
            "(нет .cod-doc/state.db — выполни alembic-миграцию проекта)",
        )
    factory = make_session_factory(engine)
    session = factory()
    db_project = ProjectRepository(session).get_by_slug(name)
    if db_project is None or db_project.row_id is None:
        session.close()
        raise HTTPException(404, f"Проект не найден в БД: {name}")
    return session, db_project.row_id


@router.get("/projects/{name}/tasks")
def list_tasks(name: str, status: str | None = None) -> list[dict[str, Any]]:
    session, project_id = _legacy_session(name)
    try:
        try:
            status_filter = TaskStatus(status) if status else None
        except ValueError:
            raise HTTPException(400, f"Неизвестный статус: {status}") from None
        tasks = task_service.list_for_project(session, project_id, status=status_filter)
        tasks.sort(key=lambda t: legacy_tasks.priority_to_int(t.priority))
        return [legacy_tasks.to_legacy_dict(t) for t in tasks]
    finally:
        session.close()


@router.post("/projects/{name}/tasks", status_code=201)
def create_task(name: str, data: TaskCreate) -> dict[str, Any]:
    session, project_id = _legacy_session(name)
    try:
        plan_id, section_id = legacy_tasks.ensure_legacy_plan(session, project_id)
        task = task_service.create(
            session,
            project_id=project_id,
            plan_id=plan_id,
            section_id=section_id,
            title=data.title,
            type=TaskType.FEATURE,
            priority=legacy_tasks.priority_from_int(data.priority),
            author=legacy_tasks.AUTHOR,
            id_prefix=legacy_tasks.LEGACY_ID_PREFIX,
            description=data.description or None,
        )
        session.commit()
        return legacy_tasks.to_legacy_dict(task)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.patch("/projects/{name}/tasks/{task_id}")
def update_task(name: str, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
    unknown = set(body) - _SUPPORTED_PATCH_FIELDS
    if unknown:
        raise HTTPException(422, f"Неподдерживаемые поля: {', '.join(sorted(unknown))}")
    session, _project_id = _legacy_session(name)
    try:
        if task_service.get(session, task_id) is None:
            raise HTTPException(404, f"Задача не найдена: {task_id}")
        if "result" in body:
            task_service.log_progress(
                session, task_id=task_id, message=str(body["result"]), author=legacy_tasks.AUTHOR
            )
        if "status" in body:
            new_status = str(body["status"])
            try:
                if new_status == TaskStatus.DONE.value:
                    task_service.complete(
                        session,
                        task_id=task_id,
                        author=legacy_tasks.AUTHOR,
                        reason=str(body.get("result") or "via legacy REST"),
                    )
                else:
                    task_service.update_status(
                        session,
                        task_id=task_id,
                        new_status=TaskStatus(new_status),
                        author=legacy_tasks.AUTHOR,
                        strict=True,
                    )
            except ValueError as exc:
                # StatusTransitionError / TaskAlreadyDoneError / TaskBlockedError
                # / неизвестный статус — всё это конфликт с контрактом машины.
                raise HTTPException(409, str(exc)) from exc
        session.commit()
        task = task_service.get(session, task_id)
        assert task is not None
        return legacy_tasks.to_legacy_dict(task)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── Agent ─────────────────────────────────────────────────────────────────────


@router.post("/projects/{name}/run")
async def run_agent(name: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
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


# ── Daemon control ────────────────────────────────────────────────────────────


@router.get("/daemon/status")
def daemon_status() -> dict[str, Any]:
    """Статус автономного агента."""
    cfg = get_config()
    return {
        "running": daemon_is_running(),
        "agent_enabled": cfg.agent_enabled,
        "projects": [
            {"name": e.name, "daemon_enabled": e.daemon_enabled} for e in cfg.list_projects()
        ],
    }


@router.post("/daemon/stop")
async def daemon_stop() -> dict[str, Any]:
    """Остановить автономного агента (без перезапуска контейнера)."""
    was_running = stop_daemon()
    return {"stopped": was_running, "running": False}


@router.post("/daemon/start")
async def daemon_start() -> dict[str, Any]:
    """Запустить автономного агента (если был остановлен)."""
    cfg = get_config()
    if not cfg.is_configured:
        raise HTTPException(400, "API-ключ не настроен")
    if not cfg.agent_enabled:
        raise HTTPException(400, "agent_enabled=False в конфиге — измени настройку сначала")
    started = start_daemon(log_callback=lambda m: logger.info(m))
    return {"started": started, "running": daemon_is_running()}
