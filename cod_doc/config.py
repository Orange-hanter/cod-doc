"""
Управление конфигурацией COD-DOC.

Конфиг хранится в ~/.cod-doc/config.yaml.
Значения можно переопределить через переменные окружения COD_DOC_*.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def config_dir() -> Path:
    """Каталог конфигурации; ``COD_DOC_HOME`` читается на КАЖДОМ вызове.

    ADO-068: раньше это была константа уровня модуля. ``cod_doc.config``
    импортируется на стадии коллекции тестов — то есть ДО того, как
    autouse-фикстура ``tests/conftest.py`` подменит ``COD_DOC_HOME`` на tmp.
    Путь замерзал на настоящем ``~/.cod-doc``, и ``Config.save()`` из тестов
    писал в рабочий конфиг пользователя: там оказались ``model: m``,
    ``api_key: sk-test`` и pytest-каталоги в ``projects:`` — побайтовая копия
    тестовых фикстур. Это был рецидив находки F5 (аудит 2026-07-29), закрытой
    ADO-001 за пять дней до того.
    """
    return Path(os.environ.get("COD_DOC_HOME", Path.home() / ".cod-doc"))


def config_file() -> Path:
    """Путь к ``config.yaml`` в актуальном :func:`config_dir`."""
    return config_dir() / "config.yaml"


# STB-011: cache the parsed config per file-path, keyed on (mtime, size).
# Config.load() is on the hot path (every MCP tool resolves a project through
# it); without this it re-reads + re-parses YAML on every call. The key is
# (mtime, size) so an external edit invalidates it; save() pops the entry so an
# in-process mutation is never served stale.
_LOAD_CACHE: dict[str, tuple[float, int, dict[str, Any]]] = {}


class ProjectEntry(BaseSettings):
    """Запись о проекте в реестре."""

    model_config = SettingsConfigDict(extra="allow")

    name: str
    path: str
    master_md: str = "MASTER.md"
    auto_commit: bool = False
    enabled: bool = True
    daemon_enabled: bool = True
    db_url: str | None = None

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

    # ADO-068: env_file здесь НЕ указан — путь к нему зависит от COD_DOC_HOME,
    # а model_config вычисляется при создании класса, то есть на импорте.
    # Подставляется в __init__ через _env_file, уже по актуальному окружению.
    model_config = SettingsConfigDict(
        env_prefix="COD_DOC_",
        env_file_encoding="utf-8",
        extra="allow",
    )

    def __init__(self, **values: object) -> None:
        """Резолвит ``.env`` в момент создания объекта, а не на импорте модуля."""
        values.setdefault("_env_file", str(config_dir() / ".env"))
        super().__init__(**values)  # type: ignore[arg-type]

    # LLM adapter selection (PCA-302, proposal 10).
    # Built-in choices: "openai_compat" (default) | "anthropic" | "mock"
    # External adapters: register in ~/.cod-doc/adapters.json
    llm_adapter: str = Field(
        default="openai_compat",
        description="LLM adapter name (openai_compat | anthropic | mock | custom)",
    )

    # OpenRouter / OpenAI-compat backend
    api_key: str = Field(default="", description="OpenRouter API key")
    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="LLM API base URL (OpenAI-compatible)",
    )
    model: str = Field(
        default="anthropic/claude-sonnet-4-6",
        description="Модель (OpenRouter model ID)",
    )
    # Optional dedicated Anthropic API key (for the 'anthropic' adapter).
    # Falls back to api_key when absent.
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key (for llm_adapter='anthropic')",
    )
    lite_model: str = Field(
        default="",
        description=(
            "Fast / cheap model for UI autocomplete (doc suggest, quick fills). "
            "Falls back to `model` when empty."
        ),
    )
    max_tokens: int = Field(default=8192)
    doc_max_tokens_heavy: int = Field(
        default=64_000,
        description=(
            "Max output tokens for deep technical docs (architecture, module-spec, "
            "module-subdoc). Modern models like Deepseek V3 / Claude Sonnet handle 64K+."
        ),
    )
    doc_max_tokens_default: int = Field(
        default=16_000,
        description=(
            "Max output tokens for all other doc types (vision, guide, standard, "
            "decision, etc.). 16K covers a thorough multi-section doc."
        ),
    )
    max_context_tokens: int = Field(
        default=100_000,
        description=(
            "Soft cap on input context tokens per task (approx len/4). "
            "Blocks exceeding the budget are skipped with a marker."
        ),
    )

    # Агент
    auto_commit: bool = Field(default=False, description="Авто-коммит после задачи")
    agent_enabled: bool = Field(
        default=True, description="Глобальный kill-switch автономного агента"
    )
    max_iterations: int = Field(default=50, description="Макс. шагов за одну задачу")
    agent_interval: int = Field(
        default=60, description="Интервал опроса задач (сек) в daemon-режиме"
    )

    # ChromaDB / Embeddings
    # ADO-068: default_factory, а не default — иначе путь замерзает на импорте.
    chroma_path: str = Field(default_factory=lambda: str(config_dir() / "chroma"))
    embedding_backend: str = Field(
        default="openai",
        description=(
            "Провайдер эмбеддингов (ADO-071, независим от llm_adapter): "
            "'openai' — generic OpenAI-совместимый /embeddings (по умолчанию); "
            "'openrouter' — OpenRouter со своим ключом и поддержкой dimensions; "
            "'local' — sentence-transformers через torch (без ключа, требует "
            "extra embeddings-local); 'mock' — детерминированный, для тестов."
        ),
    )
    # ADO-071: у эмбеддера свои реквизиты. Пустые поля означают «как раньше»
    # (для backend='openai' подставляются общие api_key/base_url), но
    # backend='openrouter' ключ у LLM НЕ наследует — это разные ключи.
    embedding_api_key: str = Field(
        default="",
        description="Выделенный ключ эмбеддера. Обязателен для backend='openrouter'.",
    )
    embedding_base_url: str = Field(
        default="",
        description=(
            "Выделенный endpoint эмбеддера. Пусто → base_url для 'openai' и "
            "https://openrouter.ai/api/v1 для 'openrouter'."
        ),
    )
    embedding_dimensions: int | None = Field(
        default=None,
        description=(
            "Размерность вектора (Matryoshka-обрезка на стороне провайдера). "
            "Пусто → нативная размерность модели. Смена значения делает "
            "существующую коллекцию несовместимой: нужен 'cod-doc embed reset'."
        ),
    )
    embedding_batch_size: int = Field(
        default=128,
        description="Сколько документов уходит в один запрос эмбеддера.",
    )
    embedding_model: str = Field(
        default="openai/text-embedding-ada-002",
        description=(
            "Embeddings model slug. For 'openai' backend: OpenRouter/OpenAI route "
            "(e.g. 'openai/text-embedding-ada-002'). For 'local' backend: a "
            "sentence-transformers model name (e.g. 'all-MiniLM-L6-v2')."
        ),
    )

    # Проекты
    projects: list[dict[str, Any]] = Field(default_factory=list)

    # API-сервер
    # SYM-003: loopback по умолчанию — auth нет, а POST /settings пишет
    # LLM-ключ. Осознанный opt-out: --host флаг, COD_DOC_BIND (см.
    # cli/cmd_serve.py) или COD_DOC_API_HOST (pydantic env, env_prefix выше).
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8765)

    @field_validator("api_key", "embedding_api_key", mode="before")
    @classmethod
    def _strip(cls, v: str) -> str:
        return str(v).strip()

    @field_validator("embedding_backend", mode="before")
    @classmethod
    def _normalize_embedding_backend(cls, v: str) -> str:
        """Неизвестный бэкенд — структурная ошибка, а не тихий фолбэк.

        Сообщение обязано называть файл: ``Config.load()`` не обёрнут в try,
        поэтому опечатка в YAML делает фатальной любую команду.
        """
        from cod_doc.core.embeddings.registry import list_embedding_adapters

        value = str(v).strip().lower() or "openai"
        # Список берём из реестра, а не из константы: внешний адаптер из
        # ~/.cod-doc/embeddings.json — легальное значение.
        allowed = list_embedding_adapters()
        if value not in allowed:
            raise ValueError(
                f"embedding_backend={v!r} в {config_file()}; допустимые значения: "
                f"{', '.join(allowed)} "
                "(внешние адаптеры регистрируются в ~/.cod-doc/embeddings.json)"
            )
        return value

    # ── Persistence ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> Config:
        """Загрузить конфиг из файла (или вернуть дефолтный).

        STB-011: the parsed YAML is cached per config-file path and invalidated
        by (mtime, size), so hot callers don't re-read+parse on every call. A
        fresh ``Config`` is still constructed each call, so callers never share
        a mutable instance.
        """
        path = config_file()
        if not path.exists():
            return cls()
        st = path.stat()
        key = str(path)
        cached = _LOAD_CACHE.get(key)
        if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
            data = cached[2]
        else:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            _LOAD_CACHE[key] = (st.st_mtime, st.st_size, data)
        return cls(**data)

    @classmethod
    def clear_cache(cls) -> None:
        """Drop the parsed-config cache (STB-011) — mainly for tests."""
        _LOAD_CACHE.clear()

    def save(self) -> None:
        """Сохранить конфиг в файл."""
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump()
        path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
        _LOAD_CACHE.pop(str(path), None)  # STB-011: invalidate stale parse

    # ── Projects ─────────────────────────────────────────────────────────────

    def get_project(self, name: str) -> ProjectEntry | None:
        for p in self.projects:
            if p.get("name") == name:
                return ProjectEntry(**p)
        return _discover_workspace_project(name)

    def discover_workspace_projects(self) -> list[ProjectEntry]:
        """Return DB-registered projects from the nearest workspace DB.

        This is intentionally read-only: it lets CLI/API commands launched from
        a repo recover when the global ``~/.cod-doc/config.yaml`` registry is
        stale, without mutating the user's personal config.
        """
        return _discover_workspace_projects()

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
        projects = [ProjectEntry(**p) for p in self.projects]
        seen = {p.name for p in projects}
        for entry in self.discover_workspace_projects():
            if entry.name not in seen:
                projects.append(entry)
                seen.add(entry.name)
        return projects

    # ── Validation ───────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    _HEAVY_DOC_TYPES = frozenset({"architecture", "module-spec", "module-subdoc"})

    def doc_token_budget(self, doc_type: str) -> int:
        """Output token budget for the given doc type.

        Architecture / module-spec / module-subdoc → ``doc_max_tokens_heavy``;
        everything else → ``doc_max_tokens_default``.
        """
        return (
            self.doc_max_tokens_heavy
            if doc_type in self._HEAVY_DOC_TYPES
            else self.doc_max_tokens_default
        )


def _nearest_workspace_db(start: Path | None = None) -> Path | None:
    """Find the closest ``.cod-doc/state.db`` from cwd upward."""
    cur = (start or Path.cwd()).resolve()
    for parent in (cur, *cur.parents):
        db_path = parent / ".cod-doc" / "state.db"
        if db_path.exists():
            return db_path
    return None


def _entry_from_project_row(slug: str, root_path: str) -> ProjectEntry | None:
    if not slug or not root_path:
        return None
    root = Path(root_path).expanduser().resolve()
    return ProjectEntry(name=slug, path=str(root), master_md="MASTER.md")


def _read_workspace_project_rows(db_path: Path) -> list[tuple[str, str]]:
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            return [
                (str(slug), str(root_path))
                for slug, root_path in conn.execute(
                    "select slug, root_path from project order by slug"
                ).fetchall()
            ]
    except sqlite3.Error:
        return []


def _discover_workspace_projects(start: Path | None = None) -> list[ProjectEntry]:
    db_path = _nearest_workspace_db(start)
    if db_path is None:
        return []
    entries: list[ProjectEntry] = []
    for slug, root_path in _read_workspace_project_rows(db_path):
        entry = _entry_from_project_row(slug, root_path)
        if entry is not None:
            entries.append(entry)
    return entries


def _discover_workspace_project(name: str) -> ProjectEntry | None:
    for entry in _discover_workspace_projects():
        if entry.name == name:
            return entry
    return None
