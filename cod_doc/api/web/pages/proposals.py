"""ACU-011 (RFC 28 §3.6): предложения фонового куратора — разбор в вебе.

Routes:
- ``GET  /p/{slug}/proposals``                      — ждущие ``doc_patch``: операция,
  обоснование, diff; ниже — открытые вопросы автора ``agent:curator``.
- ``POST /p/{slug}/proposals/{approval_id}/approve`` — одобрить: правка применяется
  в той же транзакции (ACU-009) либо approval истекает как устаревший.
- ``POST /p/{slug}/proposals/{approval_id}/deny``    — отклонить с причиной.

Пишет только ``approval_service.resolve`` от ``human:web``; ORM здесь не
трогается (RFC 26 §5.1). Ошибки и исход «устарело» возвращаются на страницу
через ``?error=`` / ``?notice=``, а не голым 400.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import QuestionStatus
from cod_doc.services import approval_service, curator_ops, curator_service, question_service

router = APIRouter()

AUTHOR = "human:web"
_PENDING_LIMIT = 200


def _diff_lines(diff: str) -> list[dict[str, str]]:
    """Строки diff с видом для подсветки: add / del / ctx."""
    lines: list[dict[str, str]] = []
    for line in diff.splitlines():
        kind = "add" if line.startswith("+") else "del" if line.startswith("-") else "ctx"
        lines.append({"kind": kind, "text": line})
    return lines


def _proposal_row(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") or {}
    return {
        "approval_id": item["approval_id"],
        "requested_at": item.get("requested_at"),
        "op": payload.get("op", "?"),
        "args": payload.get("args") or {},
        "rationale": payload.get("rationale") or "",
        "diff_lines": _diff_lines(str(payload.get("diff") or "")),
    }


def _back(slug: str, *, error: str | None = None, notice: str | None = None) -> RedirectResponse:
    url = f"/p/{slug}/proposals"
    if error:
        url += f"?error={quote(error)}"
    elif notice:
        url += f"?notice={quote(notice)}"
    return RedirectResponse(url, status_code=303)


@router.get("/p/{slug}/proposals", response_class=HTMLResponse)
def proposals_list(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    error: str | None = None,
    notice: str | None = None,
) -> HTMLResponse:
    """Ждущие предложения doc_patch и открытые вопросы куратора."""
    proj = get_project(slug)
    session, project_id = db
    page = approval_service.list_approvals(
        session,
        project_id,
        status=approval_service.PENDING,
        approval_type=approval_service.DOC_PATCH,
        limit=_PENDING_LIMIT,
    )
    questions = [
        {"question_id": q.question_id, "title": q.title, "priority": q.priority.value}
        for q in question_service.list_for_project(session, project_id, status=QuestionStatus.OPEN)
        if q.author == curator_service.CURATOR_AUTHOR
    ]
    return templates.TemplateResponse(
        request,
        "project/proposals.html",
        {
            "project": {"name": proj.entry.name},
            # Не `items`: у словаря в Jinja это метод, и цикл молча пошёл бы по нему.
            "proposals": [_proposal_row(item) for item in page["items"]],
            "total": page["total"],
            "curator_questions": questions,
            "error": error,
            "notice": notice,
        },
    )


def _resolve(
    session: Session,
    project_id: int,
    slug: str,
    approval_id: str,
    *,
    decision: str,
    comment: str | None,
) -> RedirectResponse:
    try:
        result = approval_service.resolve(
            session,
            project_id,
            approval_id,
            decision=decision,
            resolved_by=AUTHOR,
            comment=comment,
        )
        session.commit()
    except LookupError as exc:
        session.rollback()
        raise HTTPException(404, f"Предложение не найдено: {approval_id}") from exc
    except (ValueError, curator_ops.CuratorOpError) as exc:
        session.rollback()
        return _back(slug, error=str(exc))
    if result["approval"]["status"] == "expired":
        return _back(
            slug,
            notice="Правка устарела — документ изменили после предложения; ничего не записано.",
        )
    done = "Применено" if decision == "approve" else "Отклонено"
    return _back(slug, notice=f"{done}: {approval_id[:8]}")


@router.post("/p/{slug}/proposals/{approval_id}/approve")
def proposal_approve(
    slug: str,
    approval_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> RedirectResponse:
    session, project_id = db
    return _resolve(session, project_id, slug, approval_id, decision="approve", comment=None)


@router.post("/p/{slug}/proposals/{approval_id}/deny")
def proposal_deny(
    slug: str,
    approval_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    reason: Annotated[str, Form()] = "",
) -> RedirectResponse:
    session, project_id = db
    return _resolve(
        session, project_id, slug, approval_id, decision="deny", comment=reason.strip() or None
    )
