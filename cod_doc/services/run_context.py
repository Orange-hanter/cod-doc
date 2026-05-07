"""PCA-031: agent run-id propagation via contextvar.

Closes proposal 04 wiring. ``Orchestrator.run_task`` enters a run-scope
once per heartbeat; mutations made downstream (via ``RevisionService.write``,
audit-log, etc.) read the active run_id from the contextvar and stamp it
on their rows. Outside an active scope (humans / external mutations) the
contextvar holds ``None`` — column stays NULL.

Usage::

    from cod_doc.services.run_context import run_scope

    with run_scope(session, project_id, run_id="01J...", wake_reason="manual"):
        ...                # all mutations below carry run_id

Or via the orchestrator's heartbeat — see ``Orchestrator.run_task`` (PCA-022).

The contextvar is async-safe (set/reset via :class:`contextvars.Token`),
so concurrent agent runs don't bleed run_ids into each other's mutations.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.infra.models import AgentRunModel

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session


_current_run_id: ContextVar[str | None] = ContextVar("cod_doc_run_id", default=None)


def get_current_run_id() -> str | None:
    """Return the run_id active in the current async/thread context, or None."""
    return _current_run_id.get()


def set_current_run_id(run_id: str | None) -> None:
    """Override the active run_id; intended for tests / non-orchestrator contexts.

    Production code should use :func:`run_scope` so the value is reset on exit.
    """
    _current_run_id.set(run_id)


@contextmanager
def run_scope(
    session: Session,
    *,
    project_id: int,
    run_id: str,
    wake_reason: str | None = None,
    triggering_task_id: str | None = None,
    triggering_doc_ref: str | None = None,
) -> Iterator[AgentRunModel]:
    """Enter an agent-run scope: write the ``agent_run`` row, set the contextvar.

    Yields the freshly-created :class:`AgentRunModel`. On clean exit, marks
    ``status='done'`` and stamps ``finished_at``; on exception, marks
    ``status='failed'``. The caller still owns the surrounding transaction.

    Caller is responsible for committing — this helper only flushes so the
    row is queryable inside the scope.
    """
    run_model = AgentRunModel(
        run_id=run_id,
        project_id=project_id,
        wake_reason=wake_reason,
        triggering_task_id=triggering_task_id,
        triggering_doc_ref=triggering_doc_ref,
        status="running",
    )
    session.add(run_model)
    session.flush()
    token = _current_run_id.set(run_id)
    try:
        yield run_model
    except Exception:
        run_model.status = "failed"
        run_model.finished_at = datetime.now(UTC)
        session.flush()
        raise
    else:
        run_model.status = "done"
        run_model.finished_at = datetime.now(UTC)
        session.flush()
    finally:
        _current_run_id.reset(token)
