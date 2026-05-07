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
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_body: bool = False,
    ) -> dict[str, Any]:
        """List DB tasks for a project — paginated, with filters.

        Parameters
        ----------
        status:        pending | in-progress | done
        priority:      critical | high | medium | low
        limit:         max rows to return (default 50; cap large projects)
        offset:        rows to skip (for pagination)
        include_body:  if False (default), description/acceptance are omitted —
                       returns compact rows safe for context windows.

        Returns
        -------
        {"items": [...], "total": <matching count>, "limit": ..., "offset": ...}
        """
        from cod_doc.domain.entities import Priority, TaskStatus
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service

        sf, _ = session_factory(project)
        status_enum = TaskStatus(status) if status else None
        priority_enum = Priority(priority) if priority else None
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            total = task_service.count_for_project(
                session, project_id, status=status_enum, priority=priority_enum
            )
            tasks = task_service.list_for_project(
                session,
                project_id,
                status=status_enum,
                priority=priority_enum,
                limit=limit,
                offset=offset,
            )
            items = []
            for t in tasks:
                row = task_to_dict(t, session=session)
                if not include_body:
                    row.pop("description", None)
                    row.pop("acceptance", None)
                items.append(row)

        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @mcp.tool(name="task.stale")
    def task_stale(
        project: str,
        threshold_hours: float = 24.0,
    ) -> list[dict[str, Any]]:
        """List in-progress tasks idle longer than threshold_hours (default 24h).

        Surfaces stuck agents — tasks whose last revision was more than
        ``threshold_hours`` ago. Sorted oldest-first.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            tasks = task_service.list_stale_in_progress(
                session, project_id, threshold_hours=threshold_hours
            )
        return [task_to_dict(t) for t in tasks]

    @mcp.tool(name="task.log_progress")
    def task_log_progress(
        project: str,
        task_id: str,
        message: str,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Record a progress note on an in-progress task.

        Touches ``last_updated`` (so the task no longer looks stale) and
        writes a TASK revision with op=progress carrying the message.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service
        from cod_doc.services.task_service import TaskNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                t = task_service.log_progress(
                    session, task_id=task_id, message=message, author=author
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        return task_to_dict(t)

    @mcp.tool(name="task.summary")
    def task_summary(project: str) -> dict[str, Any]:
        """Aggregate task counts for a project — by status and priority.

        Cheap O(1) call (two GROUP BY queries) — safe for large projects
        where a full task.list would blow the context window.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return task_service.summarize_for_project(session, project_id)

    @mcp.tool(name="task.get")
    def task_get(project: str, task_id: str) -> dict[str, Any] | None:
        """Get a single DB task by its task_id (e.g. COD-011). Returns null if not found.

        Also accepts an 8-char YAML-hash ID (e.g. 'a27d4e9e') as a fallback —
        searches the project's tasks.yaml when not found in the DB.
        """
        import re

        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service

        sf, entry = session_factory(project)
        with transactional(sf) as session:
            t = task_service.get(session, task_id)
            if t is not None:
                return task_to_dict(t, session=session)

        # Fallback: search YAML tasks when task_id looks like an 8-char hex hash
        if re.match(r"^[0-9a-f]{8}$", task_id, re.IGNORECASE):
            from cod_doc.core.project import Project

            proj = Project(entry)
            for yaml_task in proj._load_tasks():
                if yaml_task.id == task_id:
                    return yaml_task.to_dict()

        return None

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
        blocked_by: list[str] | None = None,
        affects_files: list[str] | None = None,
        story_id: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Create a new DB task in a plan section.

        Provide either task_id (explicit, e.g. 'COD-042') or id_prefix (e.g. 'COD')
        for auto-numbering. type: feature|test|bug|refactor|migration|docs|chore.
        priority: critical|high|medium|low.

        Structured fields (C1):
        - blocked_by: list of blocking task IDs (e.g. ['COD-034'])
        - affects_files: list of paths this task touches
        - acceptance: acceptance criterion (free-text)
        - story_id: related user story ID (e.g. 'US-004')

        Duplicate guard: by default (allow_duplicate=False) the service
        rejects a new task whose normalized title matches an existing task
        in the same project; the response carries `duplicate_of`. Pass
        `allow_duplicate=True` to bypass.
        """
        from cod_doc.domain.entities import Priority, TaskType
        from cod_doc.infra.db import transactional
        from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository
        from cod_doc.services import task_service
        from cod_doc.services.task_service import DuplicateTaskError
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
                raise ValueError(f"Section '{section_letter}' not found. Available: {letters}")
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
                    affected_files=affects_files,
                    blocked_by=blocked_by,
                    story_id=story_id,
                    reason=reason,
                    allow_duplicate=allow_duplicate,
                )
                result = task_to_dict(t, session=session)
        except DuplicateTaskError as exc:
            raise ValueError(
                f"duplicate_of={exc.existing_task_id} "
                f"normalized={exc.normalized_title!r} — pass allow_duplicate=True to override"
            ) from exc
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

        return result

    @mcp.tool(name="task.set_blocker")
    def task_set_blocker(
        project: str,
        task_id: str,
        reason: str,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Mark a task as externally blocked (free-text reason).

        Distinct from task→task ``dependency`` edges; ``reason`` records
        external blockers like "waiting on stakeholder X" or "spec missing".
        Use task.clear_blocker to lift it.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service
        from cod_doc.services.task_service import TaskNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                t = task_service.set_blocker(
                    session, task_id=task_id, reason=reason, author=author
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        return task_to_dict(t)

    @mcp.tool(name="task.clear_blocker")
    def task_clear_blocker(
        project: str,
        task_id: str,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Clear the external blocker on a task (no-op if already clear)."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service
        from cod_doc.services.task_service import TaskNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                t = task_service.clear_blocker(session, task_id=task_id, author=author)
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        return task_to_dict(t)

    @mcp.tool(name="task.list_blocked")
    def task_list_blocked(
        project: str,
    ) -> list[dict[str, Any]]:
        """List tasks with an external blocker set (excluding DONE tasks)."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            tasks = task_service.list_blocked(session, project_id)
        return [task_to_dict(t) for t in tasks]

    @mcp.tool(name="task.find_duplicate")
    def task_find_duplicate(
        project: str,
        title: str,
    ) -> dict[str, Any] | None:
        """Probe for a same-title task in the project (read-only, no insert).

        Returns the existing task dict if found, else None. Comparison is
        case/punctuation/whitespace-insensitive on the normalized title.
        Useful for UI previews ("did you mean COD-042?") before submitting.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            existing = task_service.find_duplicate_by_title(session, project_id, title)
        return task_to_dict(existing) if existing else None

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
