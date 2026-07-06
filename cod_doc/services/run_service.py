"""Agent run queries for UI consumers.

Read-only helpers over the ``agent_run`` table. Web pages use this layer
instead of touching ``cod_doc.infra`` directly (architectural rule
enforced by ``tests/api/test_web_layer_imports.py``).
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

from sqlalchemy import desc, select

from cod_doc.infra.models import AgentRunModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _to_dict(r: AgentRunModel) -> dict[str, Any]:
    duration_s: float | None = None
    if r.finished_at and r.started_at:
        a = r.started_at if r.started_at.tzinfo else r.started_at.replace(tzinfo=UTC)
        b = r.finished_at if r.finished_at.tzinfo else r.finished_at.replace(tzinfo=UTC)
        duration_s = (b - a).total_seconds()
    return {
        "run_id": r.run_id,
        "status": r.status,
        "wake_reason": r.wake_reason,
        "triggering_task_id": r.triggering_task_id,
        "triggering_doc_ref": r.triggering_doc_ref,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "duration_s": duration_s,
        "summary": r.summary,
        "llm_calls": r.llm_calls,
        "llm_tokens_in": r.llm_tokens_in,
        "llm_tokens_out": r.llm_tokens_out,
    }


def list_recent(session: Session, project_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """Most recent agent runs for a project, newest first."""
    rows = (
        session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.project_id == project_id)
            .order_by(desc(AgentRunModel.started_at))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [_to_dict(r) for r in rows]


def get_one(session: Session, project_id: int, run_id: str) -> dict[str, Any] | None:
    """Fetch a single run by id, scoped to the project (returns None if absent)."""
    r = session.execute(
        select(AgentRunModel).where(
            AgentRunModel.project_id == project_id,
            AgentRunModel.run_id == run_id,
        )
    ).scalar_one_or_none()
    return _to_dict(r) if r is not None else None
