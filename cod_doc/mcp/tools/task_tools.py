"""MCP tools: task.* — DB-backed task operations (COD-032)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory, task_to_dict

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register task.* tools on the given FastMCP instance."""

    @mcp.tool(name="task.list")
    def task_list(
        project: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List DB tasks for a project. Optional status filter: pending | in-progress | done."""
        from cod_doc.domain.entities import TaskStatus
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service

        sf, _ = session_factory(project)
        status_enum = TaskStatus(status) if status else None
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            tasks = task_service.list_for_project(session, project_id, status=status_enum)
        return [task_to_dict(t) for t in tasks]

    @mcp.tool(name="task.get")
    def task_get(project: str, task_id: str) -> dict[str, Any] | None:
        """Get a single DB task by its task_id (e.g. COD-011). Returns null if not found."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            t = task_service.get(session, task_id)
        return task_to_dict(t) if t else None

    @mcp.tool(name="task.create")
    def task_create(
        project: str,
        plan_scope: str,
        section_letter: str,
        title: str,
        type: str,
        priority: str,
        task_id: str | None = None,
        id_prefix: str | None = None,
        description: str | None = None,
        acceptance: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Create a new DB task in a plan section.

        Provide either task_id (explicit, e.g. 'COD-042') or id_prefix (e.g. 'COD')
        for auto-numbering. type: feature|test|bug|refactor|migration|docs|chore.
        priority: critical|high|medium|low.
        """
        from cod_doc.domain.entities import Priority, TaskType
        from cod_doc.infra.db import transactional
        from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository
        from cod_doc.services import task_service
        from cod_doc.services.validation import ValidationError

        if task_id is None and id_prefix is None:
            raise ValueError("Provide task_id or id_prefix.")

        sf, _ = session_factory(project)

        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            plan = PlanRepository(session).get_by_scope(plan_scope)
            if plan is None or plan.row_id is None:
                raise ValueError(f"Plan '{plan_scope}' not found.")
            sections = PlanSectionRepository(session).list_for_plan(plan.row_id)
            section = next(
                (s for s in sections if s.letter.upper() == section_letter.upper()), None
            )
            if section is None or section.row_id is None:
                letters = ", ".join(s.letter for s in sections)
                raise ValueError(
                    f"Section '{section_letter}' not found. Available: {letters}"
                )
            plan_id, section_id = plan.row_id, section.row_id

        try:
            with transactional(sf) as session:
                t = task_service.create(
                    session,
                    project_id=project_id,
                    plan_id=plan_id,
                    section_id=section_id,
                    title=title,
                    type=TaskType(type),
                    priority=Priority(priority),
                    author=author,
                    task_id=task_id,
                    id_prefix=id_prefix,
                    description=description,
                    acceptance=acceptance,
                    reason=reason,
                )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

        return task_to_dict(t)

    @mcp.tool(name="task.update_status")
    def task_update_status(
        project: str,
        task_id: str,
        new_status: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Update a task's status directly. new_status: pending | in-progress | done.
        Does not validate blocking dependencies — use task.complete for guarded done transition.
        """
        from cod_doc.domain.entities import TaskStatus
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service
        from cod_doc.services.task_service import TaskNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                t = task_service.update_status(
                    session,
                    task_id=task_id,
                    new_status=TaskStatus(new_status),
                    author=author,
                    reason=reason,
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        return task_to_dict(t)

    @mcp.tool(name="task.complete")
    def task_complete(
        project: str,
        task_id: str,
        commit_sha: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Mark a task done. Validates all blocking dependencies are done first.
        Raises if the task is already done or any blocker is not yet complete.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services.task_service import (
            TaskAlreadyDoneError,
            TaskBlockedError,
            TaskNotFoundError,
            complete,
        )

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                t = complete(
                    session,
                    task_id=task_id,
                    author=author,
                    commit_sha=commit_sha,
                    reason=reason,
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        except TaskAlreadyDoneError:
            raise ValueError(f"Task '{task_id}' is already done.") from None
        except TaskBlockedError as exc:
            raise ValueError(str(exc)) from exc
        return task_to_dict(t)
