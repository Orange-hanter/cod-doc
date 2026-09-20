"""Навигатор документации: наполненность разделов.

Экран документации отвечает «где лежит», этот — «чего не написано». Оба читают
один ``doc_node``.

GET ничего не считает LLM'ом и не ходит в сеть: детерминированные пробелы
дешевы, а вердикты модели уже лежат находками в БД. Синхронный LLM-вызов на
GET был бы тем же регрессом, от которого ушёл экран документации, унеся обход
дрейфа с GET-пути (190 мс → 11 мс).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_config, get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import doc_node_health, doc_node_intent, doc_tree_service, finding_service
from cod_doc.services.ai_text import AIBackendError

router = APIRouter()

#: Находки, пришедшие от модели, помечаются в UI отдельно: у них другая цена
#: доверия, и человек вправе знать, кто это сказал.
_AI_REF = doc_node_intent.FINDING_SOURCE_REF


def _build_context(session: Session, project_id: int, slug: str) -> dict[str, Any]:
    """Разделы со своими пробелами. Ничего не пишет и не зовёт модель."""
    seeded = doc_node_health.tree_is_seeded(session, project_id)
    if not seeded:
        return {
            "project": {"name": slug},
            "seeded": False,
            "sections": [],
            "stats": {"sections": 0, "gaps": 0, "ai_gaps": 0, "unplaced": 0},
        }

    open_findings = [
        f
        for f in finding_service.list_findings(session, project_id, status="open", limit=200)
        if f["source_ref"] in {doc_node_health.FINDING_SOURCE_REF, _AI_REF}
    ]
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for finding in open_findings:
        scope_id = str((finding.get("payload") or {}).get("scope_id") or "")
        by_scope.setdefault(scope_id, []).append(
            {
                "code": finding["kind"],
                "title": finding["title"],
                "body": finding["body"],
                "severity": finding["severity"],
                "from_model": finding["source_ref"] == _AI_REF,
                "finding_uid": finding["finding_uid"],
            }
        )

    sections = [
        {
            "key": stat.node.node_key,
            "title": stat.node.title,
            "intent": stat.node.intent,
            "count": stat.doc_count,
            "min_docs": stat.node.min_docs,
            "is_inbox": stat.node.is_inbox,
            "gaps": by_scope.get(stat.node.node_key, []),
        }
        for stat in doc_tree_service.node_stats(session, project_id)
    ]
    project_gaps = by_scope.get(slug, [])

    return {
        "project": {"name": slug},
        "seeded": True,
        "sections": sections,
        "project_gaps": project_gaps,
        "stats": {
            "sections": len([s for s in sections if not s["is_inbox"]]),
            "gaps": len(open_findings),
            "ai_gaps": sum(1 for f in open_findings if f["source_ref"] == _AI_REF),
            "unplaced": doc_tree_service.unplaced_count(session, project_id),
        },
    }


@router.get("/p/{slug}/docs/navigator", response_class=HTMLResponse)
def doc_navigator(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """Наполненность разделов по уже сохранённым находкам."""
    proj = get_project(slug)
    session, project_db_id = db
    context = _build_context(session, project_db_id, proj.entry.name)
    context["analysis_error"] = ""
    return templates.TemplateResponse(request, "project/doc_navigator.html", context)


@router.post("/p/{slug}/docs/navigator/analyze", response_class=HTMLResponse)
async def doc_navigator_analyze(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """htmx: пересчитать детерминированные пробелы и спросить модель.

    Ошибка модели показывается, а не глотается: детерминированная часть уже
    записана и остаётся видна, а находки модели сохраняют прежнее состояние —
    упавший проход не вправе объявить их вылеченными.
    """
    proj = get_project(slug)
    session, project_db_id = db

    doc_node_health.sync(
        session,
        project_id=project_db_id,
        project_slug=proj.entry.name,
        author="human:web",
    )
    error = ""
    try:
        doc_node_intent.analyze(
            session,
            project_id=project_db_id,
            cfg=get_config(),
            author="human:web",
        )
    except (AIBackendError, ValueError) as exc:
        error = str(exc)
    session.commit()

    context = _build_context(session, project_db_id, proj.entry.name)
    context["analysis_error"] = error
    return templates.TemplateResponse(request, "_frag/nav_sections.html", context)
