"""
Управление конфигурацией COD-DOC.

Конфиг хранится в ~/.cod-doc/config.yaml.
Значения можно переопределить через переменные окружения COD_DOC_*.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(os.environ.get("COD_DOC_HOME", Path.home() / ".cod-doc"))
CONFIG_FILE = CONFIG_DIR / "config.yaml"


class ProjectEntry(BaseSettings):
    """Запись о проекте в реестре."""

    model_config = SettingsConfigDict(extra="allow")

    name: str
    path: str
    master_md: str = "MASTER.md"
    auto_commit: bool = False
    enabled: bool = True

    @property
    def root(self) -> Path:
        return Path(self.path).expanduser().resolve()

    @property
    def cod_doc_dir(self) -> Path:
        return self.root / ".cod-doc"

    @property
    def master_path(self) -> Path:
        return self.root / self.master_md


class Config(BaseSettings):
    """Глобальная конфигурация COD-DOC."""

    model_config = SettingsConfigDict(
        env_prefix="COD_DOC_",
        env_file=str(CONFIG_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="allow",
    )

    # OpenRouter
    api_key: str = Field(default="", description="OpenRouter API key")
    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="LLM API base URL (OpenAI-compatible)",
    )
    model: str = Field(
        default="anthropic/claude-sonnet-4-6",
        description="Модель (OpenRouter model ID)",
    )
    max_tokens: int = Field(default=8192)

    # Агент
    auto_commit: bool = Field(default=False, description="Авто-коммит после задачи")
    max_iterations: int = Field(default=50, description="Макс. шагов за одну задачу")
    agent_interval: int = Field(default=60, description="Интервал опроса задач (сек) в daemon-режиме")

    # ChromaDB
    chroma_path: str = Field(default=str(CONFIG_DIR / "chroma"))

    # Проекты
    projects: list[dict[str, Any]] = Field(default_factory=list)

    # API-сервер
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8765)

    @field_validator("api_key", mode="before")
    @classmethod
    def _strip(cls, v: str) -> str:
        return str(v).strip()

    # ── Persistence ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> "Config":
        """Загрузить конфиг из файла (или вернуть дефолтный)."""
        if CONFIG_FILE.exists():
            data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            return cls(**data)
        return cls()

    def save(self) -> None:
        """Сохранить конфиг в файл."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = self.model_dump()
        CONFIG_FILE.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))

    # ── Projects ─────────────────────────────────────────────────────────────

    def get_project(self, name: str) -> ProjectEntry | None:
        for p in self.projects:
            if p.get("name") == name:
                return ProjectEntry(**p)
        return None

    def add_project(self, entry: ProjectEntry) -> None:
        self.projects = [p for p in self.projects if p.get("name") != entry.name]
        self.projects.append(entry.model_dump())
        self.save()

    def remove_project(self, name: str) -> bool:
        before = len(self.projects)
        self.projects = [p for p in self.projects if p.get("name") != name]
        if len(self.projects) < before:
            self.save()
            return True
        return False

    def list_projects(self) -> list[ProjectEntry]:
        return [ProjectEntry(**p) for p in self.projects]

    # ── Validation ───────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)
