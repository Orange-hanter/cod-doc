"""MCP tools: task.* — DB-backed task operations (COD-032)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory, task_to_dict

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register task.* tools on the given FastMCP instance."""

    @mcp.tool(name="task_next_ready")
    def task_next_ready(
        project: str,
        plan_scope: str | None = None,
    ) -> dict[str, Any] | None:
        """Cycle-4: return the highest-priority **ready** task, or ``None``.

        Drop-in replacement for legacy ``next_pending_task`` with a
        semantically correct name (the legacy name is misleading — it
        returns *ready* tasks, not just *pending* ones).

        A task is *ready* when:
        - status is ``pending`` / ``todo``,
        - all ``blocked_by`` dependencies are ``done``,
        - it is not currently locked via ``task_checkout``.

        ``plan_scope`` (optional) restricts to a single plan.

        Use this as the input to ``task_checkout``. Returns ``None`` when
        the ready-set is empty.
        """
        from sqlalchemy import select as _select

        from cod_doc.infra.db import transactional
        from cod_doc.infra.models import TaskModel
        from cod_doc.services.plan_service import reads as plan_reads

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            try:
                project_id = require_project_id(session, project)
            except (LookupError, ValueError):
                return None

            ready = plan_reads.ready_for_project(session, project_id)
            if plan_scope is not None:
                from cod_doc.infra.repositories import PlanRepository

                plan = PlanRepository(session).get_by_scope(plan_scope)
                plan_id = plan.row_id if plan is not None else -1
                ready = [t for t in ready if getattr(t, "plan_id", None) == plan_id]
            if not ready:
                return None
            ready_ids = [t.row_id for t in ready if t.row_id is not None]
            locked = set(
                session.execute(
                    _select(TaskModel.row_id).where(
                        TaskModel.row_id.in_(ready_ids),
                        TaskModel.checked_out_by.isnot(None),
                    )
                ).scalars()
            )
            ready = [t for t in ready if t.row_id not in locked]
            if not ready:
                return None
            return task_to_dict(ready[0], session=session)

    @mcp.tool(name="task_list")
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
        status:        Any canonical TaskStatus or legacy alias. Canonical:
                       backlog | todo | in_progress | in_review | blocked |
                       done | cancelled. Legacy aliases: pending ≡ todo,
                       in-progress ≡ in_progress. Single source of truth:
                       cod_doc/services/task_status_machine.py +
                       skill `task-standard`.
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

    @mcp.tool(name="task_stale")
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

    @mcp.tool(name="task_log_progress")
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
        from cod_doc.services import checkout_service, task_service
        from cod_doc.services.task_service import TaskNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                checkout_service.warn_if_no_checkout(session, task_id, author)
                t = task_service.log_progress(
                    session, task_id=task_id, message=message, author=author
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        return task_to_dict(t)

    @mcp.tool(name="task_heartbeat_context")
    def task_heartbeat_context(
        project: str,
        task_id: str,
        since_revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Compact iteration-start snapshot for an agent (PCA-010, proposal 02).

        Replaces the ``get_master + task_get + read_context`` triple with one
        composed call: task summary, ancestry (project/plan/section/story),
        linked-docs summary (slugs/sha only, no full bodies), and a
        ``recent_changes`` list filtered by ``since_revision_id`` cursor.

        Payload stays well under 4 KB (title trimmed to 160 chars,
        blocked_by capped at 16 ids, recent_changes capped at 20 entries).
        Cold-start (no cursor) returns an empty ``recent_changes``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import heartbeat_service
        from cod_doc.services.task_service import TaskNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                require_project_id(session, project)
                return heartbeat_service.heartbeat_context(
                    session,
                    task_id=task_id,
                    since_revision_id=since_revision_id,
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None

    @mcp.tool(name="task_summary")
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

    @mcp.tool(name="task_get")
    def task_get(project: str, task_id: str) -> dict[str, Any]:
        """Get a single DB task by its task_id (e.g. COD-011).

        On hit returns the task dict (``task_id`` field present).

        On miss (PCA-938) returns a structured hint payload — never bare ``null``
        — so agents can recover without reading docs::

            {
              "task_id": null,
              "found": false,
              "requested_task_id": "<what you passed>",
              "hint": "task_id 'X' not found in project 'Y'. Try task_list to "
                      "browse or task_find_duplicate to search by title.",
              "related_tools": ["task_list", "task_find_duplicate"]
            }

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

        return {
            "task_id": None,
            "found": False,
            "requested_task_id": task_id,
            "hint": (
                f"task_id {task_id!r} not found in project {project!r}. "
                "Try task_list to browse pending tasks, or "
                "task_find_duplicate(title=...) to search by title."
            ),
            "related_tools": ["task_list", "task_find_duplicate"],
        }

    @mcp.tool(name="task_create")
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
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a new DB task in a plan section.

        Provide either task_id (explicit, e.g. 'COD-042') or id_prefix (e.g. 'COD')
        for auto-numbering. type: feature|test|bug|refactor|migration|docs|chore.
        priority: critical|high|medium|low.

        To discover valid ``section_letter`` values for a plan, call
        ``plan_sections_list(project, plan_scope)`` (PCA-941).

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
        from cod_doc.mcp.tools import _idempotency
        from cod_doc.services import task_service
        from cod_doc.services.task_service import DuplicateTaskError
        from cod_doc.services.validation import ValidationError

        if task_id is None and id_prefix is None:
            raise ValueError("Provide task_id or id_prefix.")

        # Idempotency short-circuit: if we've already seen this key for
        # task_create in this process, return the cached result.
        cached = _idempotency.check("task_create", idempotency_key)
        if cached is not None:
            return dict(cached, idempotent_replay=True)

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
            with transactional(sf, commit=not dry_run) as session:
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

        if dry_run:
            result["dry_run"] = True
        else:
            _idempotency.store("task_create", idempotency_key, result)
        # PCA-949: recommended skills based on tool-name triggers.
        # Admin-only for agent flow (AGT-010): agents using --profile agent
        # see skill bodies inlined in agent_pick().navigation.applicable_skills
        # — they don't need separate recommended_skills hints.
        from cod_doc.services.skill_service import recommend_for_tool

        recs = recommend_for_tool("task_create")
        if recs:
            result["recommended_skills"] = recs[:3]
        return result

    @mcp.tool(name="task_create_many")
    def task_create_many(
        project: str,
        plan_scope: str,
        section_letter: str,
        items: list[dict[str, Any]],
        author: str = "mcp",
        continue_on_error: bool = False,
    ) -> dict[str, Any]:
        """Create many tasks in a single transaction (PCA-946).

        Each ``items`` element accepts the same keys as ``task_create`` except
        ``project / plan_scope / section_letter / author`` (taken from outer
        args). Common shape::

            {"title": "...", "type": "feature", "priority": "medium",
             "id_prefix": "FOO", "description": "...",
             "blocked_by": [...], "story_id": "US-1"}

        Default behaviour: one transaction — any item error rolls back
        the whole batch. With ``continue_on_error=True`` errors are
        collected per-item; successful items still commit.

        Returns ``{"created": [task_dict, ...], "errors": [{"index", "title",
        "message"}, ...], "committed": bool}``.
        """
        from cod_doc.domain.entities import Priority, TaskType
        from cod_doc.infra.db import transactional
        from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository
        from cod_doc.services import task_service
        from cod_doc.services.task_service import DuplicateTaskError
        from cod_doc.services.validation import ValidationError

        sf, _ = session_factory(project)

        # Resolve plan + section once.
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            plan = PlanRepository(session).get_by_scope(plan_scope)
            if plan is None or plan.row_id is None:
                raise ValueError(f"Plan '{plan_scope}' not found.")
            sections = PlanSectionRepository(session).list_for_plan(plan.row_id)
            section = next(
                (s for s in sections if s.letter.upper() == section_letter.upper()),
                None,
            )
            if section is None or section.row_id is None:
                raise ValueError(f"Section '{section_letter}' not found in plan {plan_scope!r}")
            plan_id, section_id = plan.row_id, section.row_id

        created: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        committed = False

        def _run_in_session(session: Any) -> None:
            nonlocal created, errors
            for i, spec in enumerate(items):
                title = spec.get("title")
                if not title:
                    errors.append({"index": i, "title": None, "message": "missing 'title'"})
                    if not continue_on_error:
                        raise ValueError(f"items[{i}]: missing 'title'")
                    continue
                try:
                    t = task_service.create(
                        session,
                        project_id=project_id,
                        plan_id=plan_id,
                        section_id=section_id,
                        title=title,
                        type=TaskType(spec.get("type", "feature")),
                        priority=Priority(spec.get("priority", "medium")),
                        author=author,
                        task_id=spec.get("task_id"),
                        id_prefix=spec.get("id_prefix"),
                        description=spec.get("description"),
                        acceptance=spec.get("acceptance"),
                        affected_files=spec.get("affects_files"),
                        blocked_by=spec.get("blocked_by"),
                        story_id=spec.get("story_id"),
                        reason=spec.get("reason"),
                        allow_duplicate=spec.get("allow_duplicate", False),
                    )
                    created.append(task_to_dict(t, session=session))
                except (
                    DuplicateTaskError,
                    ValidationError,
                    ValueError,
                ) as exc:
                    errors.append({"index": i, "title": title, "message": str(exc)})
                    if not continue_on_error:
                        raise

        try:
            with transactional(sf) as session:
                _run_in_session(session)
            committed = True
        except Exception:
            committed = False

        return {"created": created, "errors": errors, "committed": committed}

    @mcp.tool(name="task_set_blocker")
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
                project_id = require_project_id(session, project)
                from cod_doc.services import checkout_service as _co

                _co.warn_if_no_checkout(session, task_id, author)
                t = task_service.set_blocker(session, task_id=task_id, reason=reason, author=author)
                from cod_doc.services import activity_service

                activity_service.emit(
                    session,
                    project_id,
                    "task.blocked",
                    actor_kind="human",
                    actor_id=author,
                    scope_kind="task",
                    scope_id=task_id,
                    payload={"reason": reason},
                    summary=f"Task {task_id} blocked: {reason[:120]}",
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        return task_to_dict(t)

    @mcp.tool(name="task_clear_blocker")
    def task_clear_blocker(
        project: str,
        task_id: str,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Clear the external blocker on a task (no-op if already clear)."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service, task_service
        from cod_doc.services.task_service import TaskNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                t = task_service.clear_blocker(session, task_id=task_id, author=author)
                activity_service.emit(
                    session,
                    project_id,
                    "task.unblocked",
                    actor_kind="human",
                    actor_id=author,
                    scope_kind="task",
                    scope_id=task_id,
                    summary=f"Task {task_id} unblocked by {author}",
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        return task_to_dict(t)

    @mcp.tool(name="task_remove_dependency")
    def task_remove_dependency(
        project: str,
        task_id: str,
        blocker_id: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Remove a task→task ``dependency`` edge (task ← blocked by ← blocker).

        Inverse of the ``blocked_by`` list accepted by ``task_create``:
        deletes the ``dependency`` row (kind='blocks', from=task_id →
        to=blocker_id). Raises if either task is unknown or no such edge
        exists (not idempotent). Writes a TASK revision
        (op=remove_dependency) and emits ``task.dependency_removed``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service
        from cod_doc.services.task_service import DependencyNotFoundError, TaskNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                require_project_id(session, project)
                t = task_service.remove_dependency(
                    session,
                    task_id=task_id,
                    blocker_task_id=blocker_id,
                    author=author,
                    reason=reason,
                )
        except TaskNotFoundError as exc:
            raise ValueError(f"Task '{exc}' not found.") from None
        except DependencyNotFoundError as exc:
            raise ValueError(str(exc)) from None
        return task_to_dict(t)

    @mcp.tool(name="task_list_blocked")
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

    @mcp.tool(name="task_find_duplicate")
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

    @mcp.tool(name="task_update_status")
    def task_update_status(
        project: str,
        task_id: str,
        new_status: str,
        author: str = "mcp",
        reason: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Update a task's status directly.

        new_status accepts the full 7-state TaskStatus taxonomy (proposal 08):

            backlog       — parked, not on the active heartbeat
            todo          — ready to work, not picked up (≡ legacy ``pending``)
            in_progress   — owned by a worker via task_checkout
                            (≡ legacy ``in-progress``; transition todo → in_progress
                             MUST go through task_checkout, not this tool)
            in_review     — explicit waiting posture (approval / review)
            blocked       — waiting on another task / external state
            done          — closed (use task.complete for guarded transition)
            cancelled     — intentionally abandoned

        Legal transitions and aliases are the single source of truth in
        cod_doc/services/task_status_machine.py (ALLOWED_TRANSITIONS).
        Raises ValueError("Invalid status transition: …") on illegal edges.

        Does not validate blocking dependencies — use task.complete for the
        guarded done transition.

        See also: skill ``task-standard`` (status semantics + when to dispatch
        a task into each bucket).
        """
        from cod_doc.domain.entities import TaskStatus
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service, task_service
        from cod_doc.services.task_service import TaskNotFoundError
        from cod_doc.services.task_status_machine import StatusTransitionError

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=not dry_run) as session:
                project_id = require_project_id(session, project)
                t = task_service.update_status(
                    session,
                    task_id=task_id,
                    new_status=TaskStatus(new_status),
                    author=author,
                    reason=reason,
                )
                activity_service.emit(
                    session,
                    project_id,
                    "task.status_changed",
                    actor_kind="agent" if author.startswith("agent") else "human",
                    actor_id=author,
                    scope_kind="task",
                    scope_id=task_id,
                    payload={"new_status": new_status, "reason": reason},
                    summary=f"Task {task_id} → {new_status}",
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        except StatusTransitionError as exc:
            raise ValueError(f"Invalid status transition: {exc}") from exc
        out = task_to_dict(t)
        if dry_run:
            out["dry_run"] = True
        return out

    @mcp.tool(name="task_update")
    def task_update(
        project: str,
        task_id: str,
        description: str | None = None,
        acceptance: str | None = None,
        priority: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Groom an existing task: rewrite description / acceptance / priority.

        ADO-067: до этого тула правка этих полей была доступна только из web —
        агент не мог переформулировать скоуп собственной задачи или
        переоценить приоритет штатным путём.

        Передавай только те поля, которые меняешь; ``None`` = «не трогать».
        Пустая строка — легальное значение (очистить поле). Хотя бы одно поле
        обязательно, иначе ValueError.

        priority: critical | high | medium | low.

        Не меняет ``title`` (идентичность задачи), ``status`` (см.
        ``task_update_status`` / ``task_checkout``) и принадлежность плану.

        Каждое изменённое поле оставляет отдельную TASK-ревизию и activity
        event; неизменившееся значение — no-op без ревизии.

        ``dry_run=True`` валидирует и возвращает результат, откатывая транзакцию.
        """
        from cod_doc.domain.entities import Priority
        from cod_doc.infra.db import transactional
        from cod_doc.services import task_service
        from cod_doc.services.task_service import TaskNotFoundError

        if description is None and acceptance is None and priority is None:
            raise ValueError(
                "task_update: pass at least one of description / acceptance / priority."
            )
        if priority is not None:
            try:
                priority_enum = Priority(priority)
            except ValueError:
                raise ValueError(
                    f"Unknown priority: {priority!r}; expected one of {[p.value for p in Priority]}"
                ) from None

        sf, _ = session_factory(project)
        changed: list[str] = []
        try:
            with transactional(sf, commit=not dry_run) as session:
                require_project_id(session, project)
                t = task_service.get(session, task_id)
                if t is None:
                    raise TaskNotFoundError(task_id)
                if description is not None:
                    t = task_service.update_description(
                        session,
                        task_id=task_id,
                        new_description=description,
                        author=author,
                        reason=reason,
                    )
                    changed.append("description")
                if acceptance is not None:
                    t = task_service.update_acceptance(
                        session,
                        task_id=task_id,
                        new_acceptance=acceptance,
                        author=author,
                        reason=reason,
                    )
                    changed.append("acceptance")
                if priority is not None:
                    t = task_service.update_priority(
                        session,
                        task_id=task_id,
                        new_priority=priority_enum,
                        author=author,
                        reason=reason,
                    )
                    changed.append("priority")
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        out = task_to_dict(t)
        out["updated_fields"] = changed
        if dry_run:
            out["dry_run"] = True
        return out

    @mcp.tool(name="task_complete")
    def task_complete(
        project: str,
        task_id: str,
        commit_sha: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Mark a task done. Validates all blocking dependencies are done first.
        Raises if the task is already done or any blocker is not yet complete.

        ``dry_run=True`` (PCA-944) validates the transition (blockers etc.)
        and returns the would-be result but rolls back the transaction.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service, checkout_service
        from cod_doc.services.task_service import (
            TaskAlreadyDoneError,
            TaskBlockedError,
            TaskNotFoundError,
            complete,
        )

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=not dry_run) as session:
                project_id = require_project_id(session, project)
                checkout_service.warn_if_no_checkout(session, task_id, author)
                t = complete(
                    session,
                    task_id=task_id,
                    author=author,
                    commit_sha=commit_sha,
                    reason=reason,
                )
                activity_service.emit(
                    session,
                    project_id,
                    "task.completed",
                    actor_kind="agent" if author.startswith("agent") else "human",
                    actor_id=author,
                    scope_kind="task",
                    scope_id=task_id,
                    payload={"commit_sha": commit_sha},
                    summary=f"Task {task_id} completed by {author}",
                )
        except TaskNotFoundError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        except TaskAlreadyDoneError:
            raise ValueError(f"Task '{task_id}' is already done.") from None
        except TaskBlockedError as exc:
            raise ValueError(str(exc)) from exc
        out = task_to_dict(t)
        if dry_run:
            out["dry_run"] = True
        return out
