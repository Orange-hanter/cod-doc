"""Legacy YAML-backed project + task management tools.

These predate the COD-032 DB-backed tools (`task_tools.py`, etc.) and stay
to support workflows that still drive the MASTER.md / tasks.yaml pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.core.project import Task, TaskStatus
from cod_doc.logging_config import get_logger

from ._legacy import load_config, open_project, project_summary, resolve_project_name

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
        project: str | None = None,
        project_name: str | None = None,
        include_tasks: bool = False,
        task_limit: int = 50,
    ) -> dict[str, Any]:
        """Return project status: stats, next actions, broken links.

        Parameters
        ----------
        project: canonical project name (preferred).
        project_name: legacy alias — accepted but emits DeprecationWarning.
        include_tasks: if True, include the YAML task list (capped at task_limit).
                       Default False to keep payload small for big projects.
        task_limit:    cap on tasks when include_tasks=True (default 50).
        """
        import re

        name = resolve_project_name(project, project_name, "get_project_status")
        proj = open_project(name)
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
    def remove_project(
        project: str | None = None,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Unregister a project from COD-DOC (does not delete files on disk).

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "remove_project")
        cfg = load_config()
        removed = cfg.remove_project(name)
        if not removed:
            raise ValueError(f"Проект не найден: {name}")
        return {"removed": name}

    @mcp.tool()
    def list_tasks(
        project: str | None = None,
        project_name: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_description: bool = False,
    ) -> dict[str, Any]:
        """List YAML-side tasks for a project — paginated, optionally filtered by status.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).

        Returns {"items": [...], "total": N, "limit": L, "offset": O}.
        With include_description=False (default) the description/result/acceptance fields
        are omitted to keep payload bounded.
        """
        name = resolve_project_name(project, project_name, "list_tasks")
        proj = open_project(name)
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
        title: str,
        project: str | None = None,
        project_name: str | None = None,
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

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        import warnings
        warnings.warn(
            "add_task (legacy) is deprecated — use task_create (DB-backed) instead",
            DeprecationWarning,
            stacklevel=1,
        )
        name = resolve_project_name(project, project_name, "add_task")
        proj = open_project(name)
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
            extra={"project": name, "task_id": task.id, "event_type": "mcp_add_task_legacy"},
        )
        return {**task.to_dict(), "_deprecated": "Use task_create instead"}

    @mcp.tool()
    def update_task(
        task_id: str,
        project: str | None = None,
        project_name: str | None = None,
        status: str | None = None,
        result: str | None = None,
        description: str | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """DEPRECATED — use mcp__cod-doc__task_update_status or update_task (DB) instead.

        Updates a legacy YAML task. This tool will be removed in a future
        release; switch to the DB-backed ``task_update_status`` MCP tool.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        import warnings
        warnings.warn(
            "update_task (legacy) is deprecated — use task_update_status (DB-backed) instead",
            DeprecationWarning,
            stacklevel=1,
        )
        name = resolve_project_name(project, project_name, "update_task")
        changes: dict[str, Any] = {}
        if status is not None:
            changes["status"] = status
        if result is not None:
            changes["result"] = result
        if description is not None:
            changes["description"] = description
        if priority is not None:
            changes["priority"] = priority

        proj = open_project(name)
        task = proj.update_task(task_id, **changes)
        if not task:
            raise ValueError(f"Задача не найдена: {task_id}")
        return {**task.to_dict(), "_deprecated": "Use task_update_status instead"}

    @mcp.tool()
    def next_pending_task(
        project: str | None = None,
        project_name: str | None = None,
        plan_scope: str | None = None,
    ) -> dict[str, Any]:
        """DEPRECATED — use ``task_next_ready`` (cycle-4 rename).

        Return the highest-priority *ready* task across a project, or null.
        Name is misleading (returns *ready*, not *pending*); kept as alias
        for backward compatibility.

        PCA-937 (cycle-3): unlike the original YAML-only implementation, this
        version respects:

        - ``blocked_by`` graph: only tasks whose blockers are all done are
          returned (sourced from the ``ready_tasks`` SQL view).
        - ``task_checkout`` locks: tasks currently locked by another agent
          are skipped.
        - optional ``plan_scope`` filter: restrict to one plan; default is
          across all plans of the project.

        Falls back to YAML ``proj.next_pending_task()`` only when the DB has
        no rows for the project (legacy YAML-only projects).

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "next_pending_task")

        # Try DB first: ready-set across all plans of the project.
        try:
            from cod_doc.infra.db import transactional
            from cod_doc.mcp.tools._db import require_project_id, session_factory, task_to_dict
            from cod_doc.services.plan_service import reads as plan_reads

            sf, _ = session_factory(name)
            with transactional(sf) as session:
                try:
                    project_id = require_project_id(session, name)
                except (LookupError, ValueError):
                    project_id = None

                if project_id is not None:
                    ready = plan_reads.ready_for_project(session, project_id)
                    # Filter: optional plan_scope match.
                    if plan_scope is not None:
                        from cod_doc.infra.repositories import PlanRepository

                        plan = PlanRepository(session).get_by_scope(plan_scope)
                        plan_id = plan.row_id if plan is not None else -1
                        ready = [t for t in ready if getattr(t, "plan_id", None) == plan_id]
                    # Filter: skip tasks currently checked out by an agent.
                    # checked_out_by lives on TaskModel, not on the domain
                    # entity — query the column directly.
                    if ready:
                        from sqlalchemy import select as _select

                        from cod_doc.infra.models import TaskModel as _TaskModel

                        ready_ids = [t.row_id for t in ready if t.row_id is not None]
                        locked = set(
                            session.execute(
                                _select(_TaskModel.row_id).where(
                                    _TaskModel.row_id.in_(ready_ids),
                                    _TaskModel.checked_out_by.isnot(None),
                                )
                            ).scalars()
                        )
                        ready = [t for t in ready if t.row_id not in locked]
                    if ready:
                        return task_to_dict(ready[0], session=session)
        except Exception:
            # Any DB-side issue: fall through to YAML legacy.
            pass

        proj = open_project(name)
        task = proj.next_pending_task()
        if not task:
            return {"task": None, "message": "Очередь задач пуста"}
        return task.to_dict()
