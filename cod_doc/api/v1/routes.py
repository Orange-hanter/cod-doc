"""REST API v1 маршруты: findings ingest, context, search (RFC 22 §3.3, SYM-006C).

Единственная поверхность будущего Bearer-гейта — см. docstring пакета
``cod_doc.api.v1``. Legacy ``/api/*`` заморожен и здесь не дублируется.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project_db
from cod_doc.api.v1.schemas import (
    ContextDepth,
    ContextResponse,
    ContextTargetKind,
    FindingIngestRequest,
    FindingIngestResponse,
    SearchResponse,
    SearchScope,
)
from cod_doc.services.activity_service import _uuid7, emit
from cod_doc.services.context_service import context_get
from cod_doc.services.finding_service import ingest_findings
from cod_doc.services.ingest_service import INGEST_ADAPTERS, lookup_adapter
from cod_doc.services.search_service import search

logger = logging.getLogger("cod_doc.api")

router = APIRouter(prefix="/api/v1")

ProjectDb = Annotated[tuple[Session, int], Depends(get_project_db)]

_TOKEN_BUDGET_MIN = 100
_TOKEN_BUDGET_MAX = 100_000
_TOKEN_BUDGET_DEFAULT = 8000
_SEARCH_LIMIT_MIN = 1
_SEARCH_LIMIT_MAX = 100
_SEARCH_LIMIT_DEFAULT = 20


def _build_source_run_id(adapter_name: str) -> str:
    """Stable, uuid7-suffixed source_run_id for ``finding_source_run`` (API path)."""
    return f"api-ingest-{adapter_name}-{_uuid7()}"


@router.post("/projects/{slug}/findings")
def post_findings(slug: str, body: FindingIngestRequest, db: ProjectDb) -> FindingIngestResponse:
    """Ingest внешнего export'а findings (тот же пайплайн, что ``cod-doc ingest``).

    Activity event ``finding.ingested`` эмитится здесь (actor_kind="api") —
    ``ingest_findings`` сам событий не пишет (write-path покрытие на уровне
    endpoint'а, как в CLI).
    """
    session, project_id = db

    try:
        adapter = lookup_adapter(body.adapter)
    except KeyError:
        raise HTTPException(
            400,
            f"Неизвестный ingest-адаптер: {body.adapter}. Доступные: {sorted(INGEST_ADAPTERS)}",
        ) from None

    try:
        raw_findings = adapter.parse(io.StringIO(json.dumps(body.export)))
        seeds = [raw.to_seed() for raw in raw_findings]
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Невалидный payload адаптера {body.adapter}: {exc}") from exc

    source_run_id = _build_source_run_id(body.adapter)
    try:
        result = ingest_findings(
            session,
            project_id=project_id,
            source_run_id=source_run_id,
            seeds=seeds,
        )
        if body.dry_run:
            session.rollback()
        else:
            emit(
                session,
                project_id=project_id,
                kind="finding.ingested",
                actor_kind="api",
                scope_kind="source_run",
                scope_id=source_run_id,
                payload={"adapter": body.adapter, **result.as_dict()},
            )
            session.commit()
    except Exception:
        session.rollback()
        raise

    total = result.created + result.updated
    logger.info(
        "v1 ingest: project=%s adapter=%s total=%d created=%d updated=%d dry_run=%s",
        slug,
        body.adapter,
        total,
        result.created,
        result.updated,
        body.dry_run,
    )
    return FindingIngestResponse(
        adapter=body.adapter,
        source_run_id=source_run_id,
        total=total,
        created=result.created,
        updated=result.updated,
        dry_run=body.dry_run,
    )


@router.get("/projects/{slug}/context")
def get_context(
    slug: str,
    db: ProjectDb,
    target_kind: ContextTargetKind,
    target_id: str,
    depth: ContextDepth = "L1",
    token_budget: int = Query(
        default=_TOKEN_BUDGET_DEFAULT, ge=_TOKEN_BUDGET_MIN, le=_TOKEN_BUDGET_MAX
    ),
) -> ContextResponse:
    """Assemble minimal-sufficient context for a target (обёртка ``context_service``)."""
    del slug  # slug уже провалидирован dependency get_project_db
    session, project_id = db
    try:
        result = context_get(
            session,
            project_id,
            target_kind,
            target_id,
            depth=depth,
            token_budget=token_budget,
        )
    except ValueError as exc:
        # target_kind/depth провалидированы pydantic — ValueError здесь = target не найден
        raise HTTPException(404, str(exc)) from exc
    return ContextResponse(**result)


@router.get("/projects/{slug}/search")
def search_v1(
    slug: str,
    db: ProjectDb,
    q: str,
    scope: SearchScope | None = None,
    limit: int = Query(default=_SEARCH_LIMIT_DEFAULT, ge=_SEARCH_LIMIT_MIN, le=_SEARCH_LIMIT_MAX),
) -> SearchResponse:
    """FTS5-поиск по проекту (обёртка ``search_service.search``)."""
    del slug  # slug уже провалидирован dependency get_project_db
    session, project_id = db
    result = search(session, project_id=project_id, query=q, scope=scope, limit=limit)
    return SearchResponse(**result)
