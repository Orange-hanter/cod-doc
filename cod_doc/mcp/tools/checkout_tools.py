"""MCP tools: task_checkout / task_release (PCA-200, proposal 06).

Atomic locks for tasks. Per the orchestrator skill: never retry on a 409
(``CheckoutConflictError``). The transition ``todo → in_progress`` MUST
go through ``task_checkout`` (the state machine refuses the direct path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import session_factory

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
        """Atomically lock a task and transition it to ``in_progress``.

        This is the ONLY legal path for the ``todo → in_progress`` transition
        per proposal 06 — call this instead of ``task_update_status``.

        agent: 'orchestrator-run-<run_id>' or 'human:<id>'.
        expected_statuses: list of allowed pre-checkout statuses; defaults to
            ['todo', 'pending']. ``pending`` is the legacy alias of ``todo``
            (see cod_doc/services/task_status_machine.py for the full 7-state
            TaskStatus taxonomy and aliases).

        Behaviours:
        - already locked by same agent → idempotent OK
        - already locked by another → ValueError (409 conflict — never retry)
        - status not in expected → ValueError (caller's plan is stale)

        On success the task's status becomes ``in_progress`` (canonical bucket).

        See also: skill ``task-standard``; cod_doc/services/task_status_machine.py.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import checkout_service
        from cod_doc.services.checkout_service import (
            CheckoutConflictError,
            CheckoutStatusError,
        )

        sf, _ = session_factory(project)
        # Событие `task.checked_out` пишет сам сервис; второй emit здесь давал
        # два события на одно действие, с разными payload.
        try:
            with transactional(sf) as session:
                result = checkout_service.checkout(
                    session,
                    task_id,
                    agent=agent,
                    expected_statuses=expected_statuses,
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
        from cod_doc.services import checkout_service
        from cod_doc.services.checkout_service import CheckoutConflictError

        sf, _ = session_factory(project)
        # `task.released` тоже пишет сервис.
        try:
            with transactional(sf) as session:
                result = checkout_service.release(
                    session,
                    task_id,
                    agent=agent,
                    force=force,
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
