"""MCP tools: run.* — inspection of built-in-orchestrator runs (PCA-032).

ADR-012 (ADO-044) снял контракт «run-id на всех мутациях»: `run_scope`
открывает только встроенный раннер (`agent/orchestrator.py`), которым
не пользуются — работа идёт через MCP из Claude Code, и там скоуп не
открывается. Замер на живой БД: `revision` 2166/2166 с `run_id IS NULL`,
`activity_event` 1114/1114, `agent_run` — одна строка от 2026-06-06.

Поэтому `run_list` и `run_revert` (и `activity_for_run`) удалены: они
построены на посылке «revision помечены run_id», которая ложна.
Остался `run_get` — точка инспекции одной строки `agent_run`; вторая
поверхность чтения — web-консоль `/p/{slug}/run`.

Helper `list_runs_for_project` сохранён: его зовёт web-слой через
`services/run_service.py`-подобный путь и он тривиально возвращает
`run_list`, если встроенный раннер снова станет рабочим.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from cod_doc.infra.models import AgentRunModel, RevisionModel
from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.orm import Session


# --------------------------------------------------------------------------- #
# Query helpers (testable without FastMCP)                                    #
# --------------------------------------------------------------------------- #


def list_runs_for_project(
    session: Session,
    project_id: int,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Newest-first runs of a project, optionally filtered by status."""
    base = select(AgentRunModel).where(AgentRunModel.project_id == project_id)
    count = (
        select(func.count())
        .select_from(AgentRunModel)
        .where(AgentRunModel.project_id == project_id)
    )
    if status is not None:
        base = base.where(AgentRunModel.status == status)
        count = count.where(AgentRunModel.status == status)
    total = int(session.execute(count).scalar_one() or 0)
    rows = session.execute(
        base.order_by(AgentRunModel.started_at.desc(), AgentRunModel.row_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars()
    return {
        "items": [_run_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_run_with_mutations(session: Session, run_id: str) -> dict[str, Any] | None:
    """Single run + revisions stamped with the same run_id.

    ADR-012: `revisions` пуст для всего, что сделано не встроенным
    раннером — это ожидаемое состояние, а не баг. Ключ `audit_log`
    убран вместе с таблицей (миграция 0029).
    """
    run = session.execute(
        select(AgentRunModel).where(AgentRunModel.run_id == run_id)
    ).scalar_one_or_none()
    if run is None:
        return None

    revs = list(
        session.execute(
            select(RevisionModel)
            .where(RevisionModel.run_id == run_id)
            .order_by(RevisionModel.at.desc(), RevisionModel.row_id.desc())
        ).scalars()
    )

    return {
        **_run_to_dict(run),
        "mutations": {
            "revisions": [
                {
                    "revision_id": r.revision_id,
                    "entity_kind": r.entity_kind,
                    "entity_id": r.entity_id,
                    "author": r.author,
                    "at": r.at.isoformat() if r.at else None,
                }
                for r in revs
            ],
        },
    }


def _run_to_dict(run: AgentRunModel) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "project_id": run.project_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "wake_reason": run.wake_reason,
        "triggering_task_id": run.triggering_task_id,
        "triggering_doc_ref": run.triggering_doc_ref,
        "llm_calls": run.llm_calls,
        "llm_tokens_in": run.llm_tokens_in,
        "llm_tokens_out": run.llm_tokens_out,
        "status": run.status,
        "summary": run.summary,
    }


# --------------------------------------------------------------------------- #
# MCP registration                                                             #
# --------------------------------------------------------------------------- #


def register(mcp: FastMCP) -> None:
    """Register run.* tools on the given FastMCP instance.

    ADR-012: остался один тул. `run_list` и `run_revert` удалены —
    см. модульный docstring.
    """

    @mcp.tool(name="run_get")
    def run_get(project: str, run_id: str) -> dict[str, Any] | None:
        """Get one built-in-orchestrator run with its linked revisions.

        Returns ``None`` when the run_id is unknown for this project.
        Мутации, сделанные через MCP / CLI / REST, run_id не несут
        (ADR-012), поэтому ``mutations.revisions`` у них пуст.
        """
        from cod_doc.infra.db import transactional

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            return get_run_with_mutations(session, run_id)
