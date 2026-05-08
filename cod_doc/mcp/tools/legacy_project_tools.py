"""Legacy YAML-backed project + task management tools.

These predate the COD-032 DB-backed tools (`task_tools.py`, etc.) and stay
to support workflows that still drive the MASTER.md / tasks.yaml pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.core.project import Task, TaskStatus
from cod_doc.logging_config import get_logger

from ._legacy import load_config, open_project, project_summary

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from cod_doc.config import ProjectEntry

log = get_logger("mcp")


def register(mcp: FastMCP) -> None:
    """Register legacy project + task management tools."""

    @mcp.tool()
    def list_projects() -> list[dict[str, Any]]:
        """List all registered COD-DOC projects with summary stats."""
        cfg = load_config()
        return [project_summary(entry) for entry in cfg.list_projects()]

    @mcp.tool()
    def get_project_status(
        project_name: str,
        include_tasks: bool = False,
        task_limit: int = 50,
    ) -> dict[str, Any]:
        """Return project status: stats, next actions, broken links.

        Parameters
        ----------
        include_tasks: if True, include the YAML task list (capped at task_limit).
                       Default False to keep payload small for big projects.
        task_limit:    cap on tasks when include_tasks=True (default 50).
        """
        import re

        proj = open_project(project_name)
        master_content = proj.read_master() or ""
        broken_links = re.findall(r"[^\n]*📁[^\n]*🔴[^\n]*", master_content)

        result: dict[str, Any] = {
            "project": project_summary(proj.entry),
            "next_actions": proj.extract_next_actions(),
            "broken_links": broken_links,
        }
        if include_tasks:
            all_tasks = proj.get_tasks()
            result["tasks"] = [t.to_dict() for t in all_tasks[:task_limit]]
            result["task_count"] = len(all_tasks)
            result["task_truncated"] = len(all_tasks) > task_limit
        return result

    @mcp.tool()
    def add_project(
        name: str,
        path: str,
        master_md: str = "MASTER.md",
        auto_commit: bool = False,
    ) -> dict[str, Any]:
        """Register a new project, init .cod-doc/ + MASTER.md template."""
        from cod_doc.config import ProjectEntry as _Entry  # avoid circular at import time

        cfg = load_config()
        entry: ProjectEntry = _Entry(
            name=name, path=path, master_md=master_md, auto_commit=auto_commit
        )
        cfg.add_project(entry)
        proj = open_project(name)
        proj.init()
        log.info("Project added via MCP", extra={"project": name, "event_type": "mcp_add_project"})
        return {"created": name, "project": project_summary(entry)}

    @mcp.tool()
    def remove_project(project_name: str) -> dict[str, Any]:
        """Unregister a project from COD-DOC (does not delete files on disk)."""
        cfg = load_config()
        removed = cfg.remove_project(project_name)
        if not removed:
            raise ValueError(f"Проект не найден: {project_name}")
        return {"removed": project_name}

    @mcp.tool()
    def list_tasks(
        project_name: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_description: bool = False,
    ) -> dict[str, Any]:
        """List YAML-side tasks for a project — paginated, optionally filtered by status.

        Returns {"items": [...], "total": N, "limit": L, "offset": O}.
        With include_description=False (default) the description/result/acceptance fields
        are omitted to keep payload bounded.
        """
        proj = open_project(project_name)
        filter_status = TaskStatus(status) if status else None
        all_tasks = proj.get_tasks(filter_status)
        page = all_tasks[offset : offset + limit]
        items: list[dict[str, Any]] = []
        for t in page:
            row = t.to_dict()
            if not include_description:
                row.pop("description", None)
                row.pop("result", None)
                row.pop("acceptance", None)
            items.append(row)
        return {"items": items, "total": len(all_tasks), "limit": limit, "offset": offset}

    @mcp.tool()
    def add_task(
        project_name: str,
        title: str,
        description: str = "",
        priority: int = 5,
        context_refs: list[str] | None = None,
        blocked_by: list[str] | None = None,
        affects_files: list[str] | None = None,
        acceptance: str | None = None,
        story_id: str | None = None,
    ) -> dict[str, Any]:
        """DEPRECATED — use mcp__cod-doc__task_create instead.

        Writes to the legacy YAML store (.cod-doc/tasks.yaml).  This tool
        will be removed in a future release; switch to ``task_create`` which
        persists tasks in the DB with full dependency tracking.
        """
        import warnings
        warnings.warn(
            "add_task (legacy) is deprecated — use task_create (DB-backed) instead",
            DeprecationWarning,
            stacklevel=1,
        )
        proj = open_project(project_name)
        task = Task(
            title=title,
            description=description,
            priority=priority,
            context_refs=context_refs or [],
            blocked_by=blocked_by or [],
            affects_files=affects_files or [],
            acceptance=acceptance,
            story_id=story_id,
        )
        proj.add_task(task)
        log.info(
            "Task added via legacy MCP (deprecated)",
            extra={"project": project_name, "task_id": task.id, "event_type": "mcp_add_task_legacy"},
        )
        return {**task.to_dict(), "_deprecated": "Use task_create instead"}

    @mcp.tool()
    def update_task(
        project_name: str,
        task_id: str,
        status: str | None = None,
        result: str | None = None,
        description: str | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """DEPRECATED — use mcp__cod-doc__task_update_status or update_task (DB) instead.

        Updates a legacy YAML task. This tool will be removed in a future
        release; switch to the DB-backed ``task_update_status`` MCP tool.
        """
        import warnings
        warnings.warn(
            "update_task (legacy) is deprecated — use task_update_status (DB-backed) instead",
            DeprecationWarning,
            stacklevel=1,
        )
        changes: dict[str, Any] = {}
        if status is not None:
            changes["status"] = status
        if result is not None:
            changes["result"] = result
        if description is not None:
            changes["description"] = description
        if priority is not None:
            changes["priority"] = priority

        proj = open_project(project_name)
        task = proj.update_task(task_id, **changes)
        if not task:
            raise ValueError(f"Задача не найдена: {task_id}")
        return {**task.to_dict(), "_deprecated": "Use task_update_status instead"}

    @mcp.tool()
    def next_pending_task(project_name: str) -> dict[str, Any]:
        """Return the highest-priority pending task, or null if the queue is empty."""
        proj = open_project(project_name)
        task = proj.next_pending_task()
        if not task:
            return {"task": None, "message": "Очередь задач пуста"}
        return task.to_dict()
