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


# --------------------------------------------------------------------------- #
# Orchestrator hooks (PCA-034)                                                 #
# --------------------------------------------------------------------------- #


def _open_session_for_project(project_path: str | None):  # type: ignore[no-untyped-def]
    """Best-effort session factory for a project on disk.

    Returns ``(session_factory, project_db_id)`` or ``(None, None)`` if
    the project has no DB (legacy YAML-only projects, fixture paths etc.).
    Used by the orchestrator hooks below — failures are silent so legacy
    test environments without a migrated DB don't break.
    """
    if not project_path:
        return None, None
    try:
        from pathlib import Path

        from cod_doc.infra.db import (
            make_engine,
            make_session_factory,
            resolve_db_url,
            transactional,
        )
        from cod_doc.infra.repositories import ProjectRepository

        url = resolve_db_url(Path(project_path))
        engine = make_engine(url)
        sf = make_session_factory(engine)
        with transactional(sf) as session:
            # Project lookup by slug — assumes the project is registered in
            # the slug-keyed cod-doc config; fixtures often skip this and
            # we silently fall through.
            from cod_doc.config import Config

            cfg = Config.load()
            for entry in cfg.list_projects():
                if entry.path == project_path:
                    proj = ProjectRepository(session).get_by_slug(entry.name)
                    if proj is not None and proj.row_id is not None:
                        return sf, proj.row_id
        return None, None
    except Exception:  # degraded path (covered by test_degraded_paths)
        return None, None


def start_orchestrator_run(
    *,
    project_path: str | None,
    run_id: str,
    wake_reason: str | None = None,
    triggering_task_id: str | None = None,
    triggering_doc_ref: str | None = None,
) -> object:
    """Begin an orchestrator run: set the contextvar + best-effort DB row.

    Returns the contextvar :class:`Token` to pass back into
    :func:`finalize_orchestrator_run`. Caller is responsible for calling
    finalize on every exit path so the contextvar resets cleanly.

    The agent_run DB row is best-effort — if the project has no DB, the
    contextvar still flows through revisions written elsewhere. This
    keeps proposal 04 working in legacy / fixture environments.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.infra.models import AgentRunModel

    sf, project_db_id = _open_session_for_project(project_path)
    if sf is not None and project_db_id is not None:
        try:
            with transactional(sf) as session:
                session.add(
                    AgentRunModel(
                        run_id=run_id,
                        project_id=project_db_id,
                        wake_reason=wake_reason,
                        triggering_task_id=triggering_task_id,
                        triggering_doc_ref=triggering_doc_ref,
                        status="running",
                    )
                )
        except Exception:  # degraded path (covered by test_degraded_paths)
            pass
    return _current_run_id.set(run_id)


def finalize_orchestrator_run(
    token: object,
    *,
    project_path: str | None,
    run_id: str,
    status: str = "done",
    summary: str | None = None,
) -> None:
    """End an orchestrator run: best-effort row update + contextvar reset.

    Always resets the contextvar (so the heartbeat doesn't leak its
    run_id into a subsequent task). DB update is best-effort.
    """
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models import AgentRunModel

    sf, project_db_id = _open_session_for_project(project_path)
    if sf is not None and project_db_id is not None:
        try:
            with transactional(sf) as session:
                run = session.execute(
                    select(AgentRunModel).where(AgentRunModel.run_id == run_id)
                ).scalar_one_or_none()
                if run is not None:
                    run.status = status
                    run.finished_at = datetime.now(UTC)
                    if summary is not None:
                        run.summary = summary
        except Exception:  # degraded path (covered by test_degraded_paths)
            pass

    from contextvars import Token

    if isinstance(token, Token):
        _current_run_id.reset(token)
    else:
        _current_run_id.set(None)
