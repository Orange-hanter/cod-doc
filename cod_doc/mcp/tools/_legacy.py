"""Shared helpers for legacy YAML-backed MCP tools (project/master/agent)."""

from __future__ import annotations

import warnings
from typing import Any

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.logging_config import get_logger

log = get_logger("mcp.legacy")


def load_config() -> Config:
    return Config.load()


def open_project(name: str) -> Project:
    cfg = load_config()
    entry = cfg.get_project(name)
    if not entry:
        raise ValueError(f"Проект не найден: {name}")
    return Project(entry)


def resolve_project_name(
    project: str | None,
    project_name: str | None,
    tool_name: str,
) -> str:
    """Resolve ``project`` ↔ ``project_name`` for legacy tool signatures (PCA-934).

    The DB-backed surface uses ``project``; legacy YAML-backed tools used
    ``project_name``. To let agents reuse a single argv shape across
    families without 422 errors, each legacy tool now accepts both:

    * ``project`` is preferred (canonical).
    * ``project_name`` still works and emits a ``DeprecationWarning``.
    * Passing both with different values is a hard error (silent confusion
      is worse than a 422).
    * Passing neither is a hard error.

    Returns the resolved project name. Internal callers continue to pass
    it as ``project_name`` to the YAML I/O layer — there's no name change
    deeper in the stack.
    """
    if project is not None and project_name is not None and project != project_name:
        raise ValueError(
            f"{tool_name}: `project` ({project!r}) and `project_name` "
            f"({project_name!r}) disagree — pass one or matching values."
        )
    resolved = project if project is not None else project_name
    if not resolved:
        raise ValueError(
            f"{tool_name}: `project` is required (legacy alias `project_name` also accepted)."
        )
    if project is None and project_name is not None:
        warnings.warn(
            f"{tool_name}: parameter `project_name` is a legacy alias; "
            f"use `project` (cod-doc DB-backed tools use `project`).",
            DeprecationWarning,
            stacklevel=3,
        )
        log.info(
            "mcp_legacy_param_alias_used",
            extra={
                "event_type": "mcp_legacy_param_alias_used",
                "tool": tool_name,
                "alias": "project_name",
                "canonical": "project",
            },
        )
    return resolved


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
