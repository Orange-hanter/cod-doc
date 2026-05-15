"""Agent orchestration + global config inspection tools."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._legacy import load_config, open_project, resolve_project_name

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from cod_doc.agent.wake_context import WakeContext
    from cod_doc.core.project import Project, Task


# --------------------------------------------------------------------------- #
# Helpers (PCA-023)                                                            #
# --------------------------------------------------------------------------- #


def _resolve_task(proj: Project, task_id: str | None) -> Task | None:
    """Return the YAML task matching ``task_id`` or the next pending one.

    The caller passes either a YAML hash-id or None; when not found, fall
    back to ``next_pending_task()`` to preserve legacy single-task behaviour.
    """
    if task_id:
        for t in proj.get_tasks():
            if t.id == task_id:
                return t
    return proj.next_pending_task()


def _build_wake_for_run(
    proj: Project,
    *,
    wake_reason: str | None,
    task_id: str | None,
    triggering_doc_ref: str | None,
    since_revision_id: str | None,
) -> WakeContext | None:
    """Translate run_agent_once trigger params into a :class:`WakeContext`.

    Returns ``None`` when ``wake_reason`` is unset (legacy behaviour).
    Opens a DB session only for reasons that need heartbeat lookup
    (task_assigned / approval_resolved); the others are session-free.
    """
    if not wake_reason:
        return None

    from cod_doc.agent.wake_context import WakeReason, build_wake_context

    reason = WakeReason(wake_reason)
    needs_session = reason in (WakeReason.TASK_ASSIGNED, WakeReason.APPROVAL_RESOLVED)
    if not needs_session:
        return build_wake_context(
            None,
            reason=reason,
            task_id=task_id,
            triggering_doc_ref=triggering_doc_ref,
            triggering_revision_id=since_revision_id,
        )

    from cod_doc.infra.db import (
        make_engine,
        make_session_factory,
        resolve_db_url,
        transactional,
    )

    url = resolve_db_url(Path(proj.entry.path))
    engine = make_engine(url)
    sf = make_session_factory(engine)
    with transactional(sf) as session:
        return build_wake_context(
            session,
            reason=reason,
            task_id=task_id,
            triggering_doc_ref=triggering_doc_ref,
            triggering_revision_id=since_revision_id,
        )


def register(mcp: FastMCP) -> None:
    """Register agent + config tools."""

    @mcp.tool()
    async def run_agent_once(
        project: str | None = None,
        project_name: str | None = None,
        autonomous: bool = True,
        task_id: str | None = None,
        wake_reason: str | None = None,
        triggering_doc_ref: str | None = None,
        since_revision_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Run the COD-DOC agent once.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).

        Modes:
        - autonomous=True (default) — agent reads MASTER, picks tasks, runs.
        - autonomous=False — single-task mode. Pass ``task_id`` (matches a
          YAML task by id) or fall back to ``next_pending_task()``.

        PCA-023 — wake-flow params (proposal 03):
        - ``wake_reason`` ∈ {cold_start, task_assigned, doc_drift,
          approval_resolved, manual}. When set, a :class:`WakeContext` is
          built and prepended as the first user-message; for scoped
          reasons (task_assigned / doc_drift / approval_resolved)
          ``MASTER.md`` is NOT loaded on the first round-trip.
        - ``task_assigned`` / ``approval_resolved`` require ``task_id``
          (looked up in the DB for heartbeat payload).
        - ``doc_drift`` requires ``triggering_doc_ref``.
        - ``since_revision_id`` is the cursor for incremental
          ``recent_changes`` in the heartbeat payload.
        """
        from cod_doc.agent.orchestrator import Orchestrator

        cfg = load_config()
        if not cfg.is_configured:
            raise ValueError("API-ключ не настроен. Запустите cod-doc wizard")

        name = resolve_project_name(project, project_name, "run_agent_once")
        proj = open_project(name)
        orch = Orchestrator(proj, cfg)
        events: list[dict[str, Any]] = []

        wake = _build_wake_for_run(
            proj,
            wake_reason=wake_reason,
            task_id=task_id,
            triggering_doc_ref=triggering_doc_ref,
            since_revision_id=since_revision_id,
        )

        if autonomous:
            gen = orch.run_autonomous()
        else:
            task = _resolve_task(proj, task_id)
            if task is None:
                return []
            gen = orch.run_task(task, wake=wake)

        async for event in gen:
            events.append(event.to_dict())
        return events

    @mcp.tool()
    def get_agent_context(
        project: str | None = None,
        project_name: str | None = None,
    ) -> list[dict[str, str]]:
        """Return the agent's conversation history (last 50 messages) for a project.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "get_agent_context")
        proj = open_project(name)
        return proj.get_context_messages()

    @mcp.tool()
    def clear_agent_context(
        project: str | None = None,
        project_name: str | None = None,
    ) -> dict[str, str]:
        """Clear the agent's conversation history for a project.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "clear_agent_context")
        proj = open_project(name)
        proj.clear_context()
        return {"cleared": name}

    @mcp.tool()
    def check_config() -> dict[str, Any]:
        """Check COD-DOC configuration status."""
        cfg = load_config()
        return {
            "is_configured": cfg.is_configured,
            "project_count": len(cfg.list_projects()),
            "api_host": cfg.api_host,
            "api_port": cfg.api_port,
        }
