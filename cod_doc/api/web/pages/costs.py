"""Cost dashboard page (PCA-925 follow-up UI).

Aggregates trace_call rows per model and applies the static pricing
dict from `openai_compat._PRICING_USD_PER_MTOK` to estimate USD cost.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import trace_service

router = APIRouter()


@router.get("/p/{slug}/costs", response_class=HTMLResponse)
def costs_dashboard(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    proj = get_project(slug)
    session, project_db_id = db
    rows = trace_service.aggregate_by_model_for_project(session, project_db_id)

    totals = {
        "calls": sum(r["calls"] for r in rows),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "duration_ms": sum(r["duration_ms"] for r in rows),
        "cost_usd": sum(r["cost_usd"] for r in rows),
    }

    return templates.TemplateResponse(
        request,
        "project/costs.html",
        {
            "project": {"name": proj.entry.name},
            "rows": rows,
            "totals": totals,
        },
    )
