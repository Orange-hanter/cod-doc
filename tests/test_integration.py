"""
Интеграционные тесты COD-DOC.

Сценарии:
  - REST API: CRUD проектов, задач, статус
  - Webhook: GitHub push → запуск агента
  - Агент: полный цикл с mock OpenRouter (respx)
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project, Task


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_project(tmp_path: Path) -> tuple[Path, ProjectEntry]:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    entry = ProjectEntry(name="integration-test", path=str(repo))
    return repo, entry


@pytest.fixture
def app_client(tmp_path: Path, tmp_project):
    """TestClient с изолированным конфигом."""
    repo, entry = tmp_project

    cfg = Config(
        api_key="sk-test-key",
        model="test/model",
        base_url="https://openrouter.ai/api/v1",
    )
    cfg.add_project(entry)

    import cod_doc.api.deps as deps
    deps.set_config(cfg)
    deps.webhook_registry.clear()

    # Инициализировать проект
    Project(entry).init()

    from cod_doc.api.server import app
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, cfg, entry


# ── API: health & config ──────────────────────────────────────────────────────

def test_health(app_client) -> None:
    client, cfg, _ = app_client
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["configured"] is True


def test_get_config_hides_api_key(app_client) -> None:
    client, _, _ = app_client
    r = client.get("/api/config")
    assert r.status_code == 200
    assert "api_key" not in r.json()


# ── API: projects ─────────────────────────────────────────────────────────────

def test_list_projects(app_client) -> None:
    client, _, entry = app_client
    r = client.get("/api/projects")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert entry.name in names


def test_get_project(app_client) -> None:
    client, _, entry = app_client
    r = client.get(f"/api/projects/{entry.name}")
    assert r.status_code == 200
    assert r.json()["name"] == entry.name
    assert r.json()["master_exists"] is True


def test_get_project_not_found(app_client) -> None:
    client, _, _ = app_client
    r = client.get("/api/projects/nonexistent")
    assert r.status_code == 404


def test_get_master(app_client) -> None:
    client, _, entry = app_client
    r = client.get(f"/api/projects/{entry.name}/master")
    assert r.status_code == 200
    assert "MASTER" in r.json()["content"] or "Navigator" in r.json()["content"]


def test_create_and_delete_project(app_client, tmp_path) -> None:
    client, _, _ = app_client
    new_repo = tmp_path / "new-repo"
    new_repo.mkdir()

    r = client.post("/api/projects", json={"name": "new-proj", "path": str(new_repo)})
    assert r.status_code == 201
    assert r.json()["created"] == "new-proj"

    r = client.delete("/api/projects/new-proj")
    assert r.status_code == 200


# ── API: tasks ────────────────────────────────────────────────────────────────

def test_create_and_list_tasks(app_client) -> None:
    client, _, entry = app_client

    r = client.post(
        f"/api/projects/{entry.name}/tasks",
        json={"title": "Тестовая задача", "priority": 2},
    )
    assert r.status_code == 201
    task = r.json()
    assert task["title"] == "Тестовая задача"
    assert task["status"] == "pending"

    r = client.get(f"/api/projects/{entry.name}/tasks")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_update_task(app_client) -> None:
    client, _, entry = app_client

    r = client.post(
        f"/api/projects/{entry.name}/tasks",
        json={"title": "Задача для обновления"},
    )
    task_id = r.json()["id"]

    r = client.patch(
        f"/api/projects/{entry.name}/tasks/{task_id}",
        json={"status": "done", "result": "выполнено"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "done"


def test_filter_tasks_by_status(app_client) -> None:
    client, _, entry = app_client
    client.post(f"/api/projects/{entry.name}/tasks", json={"title": "A"})
    r = client.post(f"/api/projects/{entry.name}/tasks", json={"title": "B"})
    task_id = r.json()["id"]
    client.patch(f"/api/projects/{entry.name}/tasks/{task_id}", json={"status": "done"})

    r = client.get(f"/api/projects/{entry.name}/tasks?status=pending")
    assert all(t["status"] == "pending" for t in r.json())

    r = client.get(f"/api/projects/{entry.name}/tasks?status=done")
    assert all(t["status"] == "done" for t in r.json())


# ── Webhook ───────────────────────────────────────────────────────────────────

def test_register_and_list_webhook(app_client) -> None:
    client, _, entry = app_client

    r = client.post("/api/webhooks", json={
        "project_name": entry.name,
        "repo_url": "https://github.com/owner/repo",
        "secret": "mysecret",
    })
    assert r.status_code == 201

    r = client.get("/api/webhooks")
    assert any(w["repo_url"] == "https://github.com/owner/repo" for w in r.json())


def test_github_webhook_triggers_agent(app_client) -> None:
    client, _, entry = app_client

    secret = "test-secret"
    client.post("/api/webhooks", json={
        "project_name": entry.name,
        "repo_url": "https://github.com/owner/repo",
        "secret": secret,
    })

    payload = json.dumps({
        "ref": "refs/heads/main",
        "repository": {"html_url": "https://github.com/owner/repo"},
    }).encode()

    sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    with patch("cod_doc.api.webhooks.Orchestrator") as MockOrch:
        async def _fake_run():
            from cod_doc.agent.orchestrator import AgentEvent
            yield AgentEvent("done", "ok")

        MockOrch.return_value.run_autonomous = _fake_run

        r = client.post(
            "/api/webhook/github",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-Github-Event": "push",
            },
        )

    assert r.status_code == 200
    assert r.json()["triggered"] is True
    assert r.json()["project"] == entry.name


def test_github_webhook_wrong_signature(app_client) -> None:
    client, _, entry = app_client
    client.post("/api/webhooks", json={
        "project_name": entry.name,
        "repo_url": "https://github.com/owner/repo2",
        "secret": "real-secret",
    })

    payload = json.dumps({"ref": "refs/heads/main", "repository": {"html_url": "https://github.com/owner/repo2"}}).encode()

    r = client.post(
        "/api/webhook/github",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=wrongsignature",
            "X-Github-Event": "push",
        },
    )
    assert r.status_code == 403


def test_github_webhook_skips_non_push(app_client) -> None:
    client, _, entry = app_client
    client.post("/api/webhooks", json={
        "project_name": entry.name,
        "repo_url": "https://github.com/owner/repo3",
        "secret": "",
    })

    payload = json.dumps({"repository": {"html_url": "https://github.com/owner/repo3"}}).encode()

    r = client.post(
        "/api/webhook/github",
        content=payload,
        headers={"Content-Type": "application/json", "X-Github-Event": "pull_request"},
    )
    assert r.status_code == 200
    assert r.json()["skipped"] is True


# ── Полный цикл агента через API ──────────────────────────────────────────────

def test_agent_run_full_cycle(app_client) -> None:
    """
    POST /api/projects/{name}/tasks  →  POST /api/projects/{name}/run
    Мокируем OpenRouter через patch на Orchestrator.run_autonomous.
    Проверяем что задача создана и background task запущен.
    """
    client, _, entry = app_client

    # Создать задачу
    r = client.post(
        f"/api/projects/{entry.name}/tasks",
        json={"title": "Full cycle task", "priority": 1},
    )
    assert r.status_code == 201

    # Запустить агента (background task в TestClient выполняется синхронно)
    with patch("cod_doc.api.routes.Orchestrator") as MockOrch:
        async def _fake_auto():
            from cod_doc.agent.orchestrator import AgentEvent
            yield AgentEvent("thinking", "start")
            yield AgentEvent("done", "Task complete")

        MockOrch.return_value.run_autonomous = _fake_auto

        r = client.post(f"/api/projects/{entry.name}/run")

    assert r.status_code == 200
    assert r.json()["started"] is True
