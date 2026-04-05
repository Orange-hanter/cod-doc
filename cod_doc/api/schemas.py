"""Pydantic-схемы для API."""

from __future__ import annotations

from pydantic import BaseModel


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


class WebhookRegister(BaseModel):
    """Регистрация webhook для проекта."""
    project_name: str
    repo_url: str
    secret: str = ""
