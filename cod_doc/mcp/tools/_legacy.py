"""Shared helpers for legacy YAML-backed MCP tools (project/master/agent)."""

from __future__ import annotations

from typing import Any

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project


def load_config() -> Config:
    return Config.load()


def open_project(name: str) -> Project:
    cfg = load_config()
    entry = cfg.get_project(name)
    if not entry:
        raise ValueError(f"Проект не найден: {name}")
    return Project(entry)


def project_summary(entry: ProjectEntry) -> dict[str, Any]:
    proj = Project(entry)
    return {
        "name": entry.name,
        "path": entry.path,
        "master_md": entry.master_md,
        "auto_commit": entry.auto_commit,
        "enabled": entry.enabled,
        "master_exists": entry.master_path.exists(),
        "stats": proj.stats(),
    }
