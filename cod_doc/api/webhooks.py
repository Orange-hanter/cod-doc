"""Webhook и WebSocket маршруты."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, WebSocket, WebSocketDisconnect

from cod_doc.agent.orchestrator import Orchestrator
from cod_doc.api.deps import get_config, get_project, webhook_registry
from cod_doc.api.schemas import WebhookRegister

logger = logging.getLogger("cod_doc.api")

router = APIRouter(prefix="/api")


# ── Webhook management ────────────────────────────────────────────────────────

@router.post("/webhooks", status_code=201)
def register_webhook(data: WebhookRegister) -> dict:
    cfg = get_config()
    if not cfg.get_project(data.project_name):
        raise HTTPException(404, f"Проект не найден: {data.project_name}")
    webhook_registry[data.repo_url] = {
        "project": data.project_name,
        "secret": data.secret,
    }
    return {"registered": data.repo_url, "project": data.project_name}


@router.get("/webhooks")
def list_webhooks() -> list[dict]:
    return [
        {"repo_url": url, "project": info["project"], "has_secret": bool(info["secret"])}
        for url, info in webhook_registry.items()
    ]


@router.delete("/webhooks")
def delete_webhook(repo_url: str) -> dict:
    if repo_url not in webhook_registry:
        raise HTTPException(404, f"Webhook не найден: {repo_url}")
    del webhook_registry[repo_url]
    return {"deleted": repo_url}


# ── GitHub webhook ────────────────────────────────────────────────────────────

@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str = Header(default="push"),
) -> dict:
    """Принять GitHub push webhook и запустить агента для соответствующего проекта."""
    body = await request.body()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(400, "Невалидный JSON payload")

    repo_url: str = (
        payload.get("repository", {}).get("html_url", "")
        or payload.get("repository", {}).get("url", "")
    )

    entry = webhook_registry.get(repo_url)
    if not entry:
        for alt_key in ("ssh_url", "clone_url", "git_url"):
            alt = payload.get("repository", {}).get(alt_key, "")
            if alt and alt in webhook_registry:
                entry = webhook_registry[alt]
                break

    if not entry:
        logger.warning(f"Webhook: репозиторий не зарегистрирован: {repo_url}")
        raise HTTPException(404, f"Репозиторий не зарегистрирован: {repo_url}")

    secret: str = entry.get("secret", "")
    if secret:
        if not x_hub_signature_256:
            raise HTTPException(403, "Отсутствует подпись X-Hub-Signature-256")
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, x_hub_signature_256):
            raise HTTPException(403, "Неверная подпись webhook")

    if x_github_event != "push":
        return {"skipped": True, "event": x_github_event}

    project_name: str = entry["project"]
    branch = payload.get("ref", "").replace("refs/heads/", "")
    logger.info(
        "Webhook push получен",
        extra={"project": project_name, "event_type": "webhook", "tool": branch},
    )

    cfg = get_config()
    if not cfg.is_configured:
        raise HTTPException(400, "API-ключ не настроен")

    proj = get_project(project_name)

    async def _run() -> None:
        orch = Orchestrator(proj, cfg)
        async for event in orch.run_autonomous():
            logger.info(
                str(event.data)[:200],
                extra={"project": project_name, "event_type": event.type},
            )

    background_tasks.add_task(_run)
    return {"triggered": True, "project": project_name, "branch": branch}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@router.websocket("/ws/projects/{name}/run")
async def ws_run_agent(websocket: WebSocket, name: str) -> None:
    """WebSocket для стриминга вывода агента в реальном времени."""
    await websocket.accept()
    try:
        proj = get_project(name)
        cfg = get_config()
        if not cfg.is_configured:
            await websocket.send_json({"type": "error", "data": "API-ключ не настроен"})
            return

        orch = Orchestrator(proj, cfg)
        async for event in orch.run_autonomous():
            await websocket.send_json(event.to_dict())
            await asyncio.sleep(0)

        await websocket.send_json({"type": "done", "data": "Завершено"})
    except WebSocketDisconnect:
        logger.info(f"WebSocket отключён: {name}")
    except HTTPException as e:
        await websocket.send_json({"type": "error", "data": e.detail})
    except Exception as e:
        await websocket.send_json({"type": "error", "data": str(e)})
    finally:
        await websocket.close()
