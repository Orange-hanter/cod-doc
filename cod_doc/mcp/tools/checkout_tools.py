"""MCP tools: task_checkout / task_release (PCA-200, proposal 06).

Atomic locks for tasks. Per the orchestrator skill: never retry on a 409
(``CheckoutConflictError``). The transition ``todo → in_progress`` MUST
go through ``task_checkout`` (the state machine refuses the direct path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.tool(name="task_checkout")
    def task_checkout(
        project: str,
        task_id: str,
        agent: str,
        expected_statuses: list[str] | None = None,
    ) -> dict[str, Any]:
        """Atomically lock a task and transition to in_progress.

        agent: 'orchestrator-run-<run_id>' or 'human:<id>'.
        expected_statuses: list of allowed pre-checkout statuses;
            defaults to ['todo', 'pending'].

        Behaviours:
        - already locked by same agent → idempotent OK
        - already locked by another → ValueError (409 conflict — never retry)
        - status not in expected → ValueError (caller's plan is stale)
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import checkout_service, activity_service
        from cod_doc.services.checkout_service import (
            CheckoutConflictError,
            CheckoutStatusError,
        )

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                result = checkout_service.checkout(
                    session, task_id, agent=agent, expected_statuses=expected_statuses,
                )
                if not result.idempotent:
                    activity_service.emit(
                        session, project_id, "task.checked_out",
                        actor_kind="orchestrator" if "run" in agent else "human",
                        actor_id=agent,
                        scope_kind="task", scope_id=task_id,
                        payload={
                            "from_status": result.expected_status_at_checkout,
                            "to_status": result.new_status,
                        },
                        summary=f"Task {task_id} checked out by {agent}",
                    )
        except LookupError as exc:
            raise ValueError(str(exc)) from exc
        except (CheckoutConflictError, CheckoutStatusError) as exc:
            raise ValueError(str(exc)) from exc
        return {
            "task_id": result.task_id,
            "checked_out_by": result.checked_out_by,
            "checked_out_at": result.checked_out_at.isoformat() if result.checked_out_at else None,
            "expected_status_at_checkout": result.expected_status_at_checkout,
            "new_status": result.new_status,
            "idempotent": result.idempotent,
        }

    @mcp.tool(name="task_release")
    def task_release(
        project: str,
        task_id: str,
        agent: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Release the lock held by ``agent``.

        force=True: release regardless of who holds the lock (admin).
        Status is unchanged — call task_complete or task_update_status separately.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import checkout_service, activity_service
        from cod_doc.services.checkout_service import CheckoutConflictError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                result = checkout_service.release(
                    session, task_id, agent=agent, force=force,
                )
                if not result.idempotent:
                    activity_service.emit(
                        session, project_id, "task.released",
                        actor_kind="human" if force else "orchestrator",
                        actor_id=agent,
                        scope_kind="task", scope_id=task_id,
                        payload={"force": force},
                        summary=f"Task {task_id} released by {agent}",
                    )
        except LookupError as exc:
            raise ValueError(str(exc)) from exc
        except CheckoutConflictError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "task_id": result.task_id,
            "checked_out_by": result.checked_out_by,
            "new_status": result.new_status,
            "idempotent": result.idempotent,
        }
