"""MCP tools: routine.* — cron-style health checks (PCA-211, proposal 07).

Tools
-----
- ``routine_create``        — register a new routine
- ``routine_list``          — list project routines
- ``routine_get``           — single routine + recent run summary
- ``routine_update_status`` — enable/disable a routine
- ``routine_delete``        — delete a routine
- ``routine_run_now``       — manually fire (returns RoutineRun row)
- ``routine_history``       — recent runs
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _routine_to_dict(r: Any) -> dict[str, Any]:
    return {
        "name": r.name,
        "enabled": r.enabled,
        "trigger": r.trigger,
        "cron": r.cron,
        "check_name": r.check_name,
        "check_args": r.check_args,
        "on_finding": r.on_finding,
        "concurrency": r.concurrency,
        "catch_up": r.catch_up,
        "created": r.created.isoformat() if r.created else None,
        "last_updated": r.last_updated.isoformat() if r.last_updated else None,
    }


def _run_to_dict(r: Any) -> dict[str, Any]:
    return {
        "row_id": r.row_id,
        "routine_id": r.routine_id,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "status": r.status,
        "findings_count": r.findings_count,
        "created_task_id": r.created_task_id,
        "error": r.error,
        "run_id": r.run_id,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(name="routine_create")
    def routine_create(
        project: str,
        name: str,
        check_name: str,
        trigger: str = "cron",
        cron: str | None = None,
        check_args: dict[str, Any] | None = None,
        on_finding: str = "comment_only",
        concurrency: str = "skip",
        catch_up: str = "run_latest",
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Register a new routine.

        check_name: one of {approval_stale, stale_refs, link_integrity,
                            doc_drift, task_stale}
        trigger: 'cron' | 'manual' | 'event'  (cron requires `cron` expr).
        on_finding: 'create_task' | 'update_existing_task' | 'comment_only'.
        concurrency: 'skip' | 'queue' | 'parallel'.
        catch_up: 'skip' | 'run_latest' | 'run_all'.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import routine_service

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                r = routine_service.create(
                    session,
                    project_id,
                    name=name,
                    check_name=check_name,
                    trigger=trigger,
                    cron=cron,
                    check_args=check_args,
                    on_finding=on_finding,
                    concurrency=concurrency,
                    catch_up=catch_up,
                    enabled=enabled,
                )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return _routine_to_dict(r)

    @mcp.tool(name="routine_list")
    def routine_list(project: str, enabled_only: bool = False) -> list[dict[str, Any]]:
        """List all routines in a project."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import routine_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            rows = routine_service.list_routines(session, project_id, enabled_only=enabled_only)
        return [_routine_to_dict(r) for r in rows]

    @mcp.tool(name="routine_get")
    def routine_get(project: str, name: str) -> dict[str, Any] | None:
        """Get a single routine by name (returns null if not found)."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import routine_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            r = routine_service.get(session, project_id, name)
        return _routine_to_dict(r) if r is not None else None

    @mcp.tool(name="routine_update_status")
    def routine_update_status(
        project: str,
        name: str,
        enabled: bool,
    ) -> dict[str, Any]:
        """Enable or disable a routine without removing it."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import routine_service
        from cod_doc.services.routine_service import RoutineNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                r = routine_service.update_status(
                    session,
                    project_id,
                    name,
                    enabled=enabled,
                )
        except RoutineNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return _routine_to_dict(r)

    @mcp.tool(name="routine_delete")
    def routine_delete(project: str, name: str) -> dict[str, Any]:
        """Delete a routine (cascades to routine_run history)."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import routine_service
        from cod_doc.services.routine_service import RoutineNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                routine_service.delete(session, project_id, name)
        except RoutineNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return {"deleted": name}

    @mcp.tool(name="routine_run_now")
    def routine_run_now(project: str, name: str) -> dict[str, Any]:
        """Manually fire a routine; returns the RoutineRun row.

        Concurrency 'skip': returns the existing 'running' row if any,
        without starting a new run.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import routine_service
        from cod_doc.services.routine_service import RoutineNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                run = routine_service.run_now(session, project_id, name)
        except RoutineNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return _run_to_dict(run)

    @mcp.tool(name="routine_history")
    def routine_history(
        project: str,
        name: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Recent RoutineRun rows for a routine, newest first."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import routine_service
        from cod_doc.services.routine_service import RoutineNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                rows = routine_service.history(session, project_id, name, limit=limit)
        except RoutineNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return [_run_to_dict(r) for r in rows]
