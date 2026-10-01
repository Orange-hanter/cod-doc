"""OQM-004: web pages for open questions.

Routes:
- ``GET  /p/{slug}/questions``                          — list with filters + counters.
- ``GET  /p/{slug}/questions/new``                      — create form.
- ``POST /p/{slug}/questions/new``                      — submit; redirect to detail.
- ``GET  /p/{slug}/questions/{qid}``                    — card: question, context,
  options, links (verify badge + code excerpt), resolution, forms.
- ``POST /p/{slug}/questions/{qid}/edit``               — patch fields.
- ``POST /p/{slug}/questions/{qid}/resolve``            — close with an answer.
- ``POST /p/{slug}/questions/{qid}/drop``               — close without an answer.
- ``POST /p/{slug}/questions/{qid}/reopen``             — back to open.
- ``POST /p/{slug}/questions/{qid}/options``            — add an option.
- ``POST /p/{slug}/questions/{qid}/options/{position}`` — edit or remove an option.
- ``POST /p/{slug}/questions/{qid}/links``              — attach or detach a link.
- ``POST /p/{slug}/questions/{qid}/verify``             — re-check this question's links.

Unlike scenarios, questions are editable here: they have no markdown
projection, so the web form is not a fourth writer competing with a file —
the DB row is the only copy. Every write goes through ``question_service``
with ``author="human:web"``.

Service errors (validation, wrong state) redirect back to the card with
``?error=…`` instead of a bare 400, so the message lands next to the form.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.markdown import autolink_adr_refs, render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import (
    Priority,
    QuestionLinkKind,
    QuestionRelation,
    QuestionStatus,
)
from cod_doc.services import question_service
from cod_doc.services.question_service import (
    QuestionAlreadyExistsError,
    QuestionNotFoundError,
    QuestionOptionNotFoundError,
    QuestionStateError,
)

if TYPE_CHECKING:
    from cod_doc.domain.entities import OpenQuestion

router = APIRouter()

AUTHOR = "human:web"

STATUS_OPTIONS = [s.value for s in QuestionStatus]
STATUS_FILTERS = [*STATUS_OPTIONS, "all"]
PRIORITY_OPTIONS = [p.value for p in Priority]
LINK_KIND_OPTIONS = [k.value for k in QuestionLinkKind]
RELATION_OPTIONS = [r.value for r in QuestionRelation]

#: Подписи для UI. Эмодзи-иконки видов ссылок выброшены: часть из них
#: рендерится пустым квадратом, а текстовая метка вида читается однозначно.
STATUS_LABEL = {"open": "Открыт", "resolved": "Решён", "dropped": "Снят"}
STATUS_TAB_LABEL = {
    "open": "Открытые",
    "resolved": "Решённые",
    "dropped": "Снятые",
    "all": "Все",
}
RELATION_LABEL = {
    "about": "о чём",
    "blocks": "блокирует",
    "addressed_by": "решается в",
    "resolved_by": "решён в",
    "see_also": "см. также",
}
_EXCERPT_CHARS = 140

_SERVICE_ERRORS = (
    QuestionAlreadyExistsError,
    QuestionStateError,
    QuestionOptionNotFoundError,
    ValueError,
)


def _prose(text: str | None, slug: str) -> str:
    return autolink_adr_refs(render_markdown(text), slug=slug) if text else ""


def _link_href(slug: str, to_kind: str, to_ref: str) -> str:
    """Route for a linked target; ``""`` when the target has no page (code, finding)."""
    base = f"/p/{slug}"
    doc_key, _, anchor = to_ref.partition("#")
    routes = {
        "document": f"{base}/docs/{to_ref}",
        "section": f"{base}/docs/{doc_key}#{anchor}",
        "task": f"{base}/tasks/{to_ref}",
        "adr": f"{base}/adr/{to_ref}",
        "story": f"{base}/stories/{to_ref}",
        "scenario": f"{base}/scenarios/{to_ref}",
        "url": to_ref,
    }
    return routes.get(to_kind, "")


def _plain_excerpt(text: str) -> str:
    """Первая строка вопроса без markdown-разметки — подзаголовок в списке."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = line.replace("**", "").replace("__", "").replace("`", "").lstrip("#> ").strip()
    return line if len(line) <= _EXCERPT_CHARS else line[: _EXCERPT_CHARS - 1] + "…"


def _row(q: OpenQuestion) -> dict[str, Any]:
    return {
        **question_service.question_summary(q),
        "status_label": STATUS_LABEL.get(q.status.value, q.status.value),
        "excerpt": _plain_excerpt(q.question) if q.question.strip() != q.title else "",
    }


def _back(slug: str, qid: str, error: str | None = None) -> RedirectResponse:
    url = f"/p/{slug}/questions/{qid}"
    if error:
        url += f"?error={quote(error)}"
    return RedirectResponse(url=url, status_code=303)


def _mutate(
    session: Session,
    slug: str,
    qid: str,
    action: Callable[[], object],
    *,
    anchor: str = "",
) -> RedirectResponse:
    """Run one service write; commit and go back to the card, or carry the error."""
    try:
        action()
        session.commit()
    except QuestionNotFoundError as exc:
        session.rollback()
        raise HTTPException(404, f"Вопрос не найден: {qid}") from exc
    except _SERVICE_ERRORS as exc:
        session.rollback()
        return _back(slug, qid, str(exc))
    response = _back(slug, qid)
    if anchor:
        response.headers["location"] += f"#{anchor}"
    return response


# --------------------------------------------------------------------------- #
# list + create                                                                #
# --------------------------------------------------------------------------- #


@router.get("/p/{slug}/questions", response_class=HTMLResponse)
def questions_list(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    status: str = "open",
    priority: str | None = None,
    owner: str | None = None,
) -> HTMLResponse:
    """Open questions first, by priority; counters over the whole project."""
    proj = get_project(slug)
    session, project_id = db
    if status not in STATUS_FILTERS:
        status = "open"
    if priority not in PRIORITY_OPTIONS:
        priority = None

    everything = question_service.list_for_project(session, project_id)
    shown = question_service.list_for_project(
        session,
        project_id,
        status=None if status == "all" else QuestionStatus(status),
        priority=Priority(priority) if priority else None,
        owner=owner or None,
    )
    broken = question_service.broken_links(session, project_id=project_id)
    broken_by_question: dict[str, int] = {}
    for b in broken:
        broken_by_question[b.question_id] = broken_by_question.get(b.question_id, 0) + 1

    rows = []
    for q in shown:
        assert q.row_id is not None
        row = _row(q)
        row["broken"] = broken_by_question.get(q.question_id, 0)
        row["options"] = len(question_service.list_options(session, q.row_id))
        row["links"] = len(question_service.list_links(session, q.row_id))
        rows.append(row)

    counts = {s: sum(1 for q in everything if q.status.value == s) for s in STATUS_OPTIONS}
    counts["all"] = len(everything)
    tabs = [{"value": f, "label": STATUS_TAB_LABEL[f], "count": counts[f]} for f in STATUS_FILTERS]
    return templates.TemplateResponse(
        request,
        "project/questions_list.html",
        {
            "project": proj.entry,
            "rows": rows,
            "counts": counts,
            "broken_total": len(broken),
            "status_filter": status,
            "priority_filter": priority,
            "owner_filter": owner or "",
            "tabs": tabs,
            "priority_options": PRIORITY_OPTIONS,
            "owners": sorted({q.owner for q in everything if q.owner}),
        },
    )


@router.get("/p/{slug}/questions/new", response_class=HTMLResponse)
def question_new_form(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    error: str | None = None,
) -> HTMLResponse:
    proj = get_project(slug)
    return templates.TemplateResponse(
        request,
        "project/question_new.html",
        {
            "project": proj.entry,
            "priority_options": PRIORITY_OPTIONS,
            "link_kind_options": LINK_KIND_OPTIONS,
            "relation_options": RELATION_OPTIONS,
            "error": error,
        },
    )


@router.post("/p/{slug}/questions/new")
def question_new_submit(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    title: Annotated[str, Form()],
    question: Annotated[str, Form()],
    context: Annotated[str | None, Form()] = None,
    priority: Annotated[str, Form()] = "medium",
    owner: Annotated[str | None, Form()] = None,
    options: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Create; ``options`` is one option title per line."""
    session, project_id = db
    try:
        created = question_service.create(
            session,
            project_id=project_id,
            title=title.strip(),
            question=question.strip(),
            context=(context or "").strip() or None,
            priority=Priority(priority),
            owner=(owner or "").strip() or None,
            options=[(line.strip(), None) for line in (options or "").splitlines() if line.strip()],
            author=AUTHOR,
        )
        session.commit()
    except _SERVICE_ERRORS as exc:
        session.rollback()
        return RedirectResponse(
            url=f"/p/{slug}/questions/new?error={quote(str(exc))}", status_code=303
        )
    return _back(slug, created.question_id)


# --------------------------------------------------------------------------- #
# card                                                                         #
# --------------------------------------------------------------------------- #


@router.get("/p/{slug}/questions/{qid}", response_class=HTMLResponse)
def question_show(
    request: Request,
    slug: str,
    qid: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    error: str | None = None,
    edit: int = 0,
) -> HTMLResponse:
    proj = get_project(slug)
    session, project_id = db
    found = question_service.get(session, project_id, qid)
    if found is None or found.row_id is None:
        raise HTTPException(404, f"Вопрос не найден: {qid}")

    card = question_service.question_to_dict(session, found)
    card.update(_row(found))
    card["question_html"] = _prose(found.question, slug)
    card["context_html"] = _prose(found.context, slug)
    card["resolution_html"] = _prose(found.resolution, slug)
    for opt in card["options"]:
        opt["body_html"] = _prose(opt["body"], slug)
    for edge in card["links"]:
        edge["href"] = _link_href(slug, edge["to_kind"], edge["to_ref"])
        edge["relation_label"] = RELATION_LABEL.get(edge["relation"], edge["relation"])
        edge["excerpt"] = (
            question_service.code_excerpt(session, project_id, edge["to_ref"])
            if edge["to_kind"] == "code"
            else None
        )

    return templates.TemplateResponse(
        request,
        "project/question_show.html",
        {
            "project": proj.entry,
            "q": card,
            "status_label": STATUS_LABEL.get(card["status"], card["status"]),
            "broken_links": sum(1 for e in card["links"] if e["resolved"] is False),
            "row_id": found.row_id,
            "error": error,
            "edit": bool(edit),
            "priority_options": PRIORITY_OPTIONS,
            "link_kind_options": LINK_KIND_OPTIONS,
            "relation_options": RELATION_OPTIONS,
        },
    )


@router.post("/p/{slug}/questions/{qid}/edit")
def question_edit(
    slug: str,
    qid: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    title: Annotated[str, Form()],
    question: Annotated[str, Form()],
    context: Annotated[str, Form()] = "",
    priority: Annotated[str, Form()] = "medium",
    owner: Annotated[str, Form()] = "",
) -> RedirectResponse:
    session, project_id = db
    return _mutate(
        session,
        slug,
        qid,
        lambda: question_service.update(
            session,
            project_id=project_id,
            question_id=qid,
            title=title.strip(),
            question=question.strip(),
            context=context.strip(),
            priority=Priority(priority),
            owner=owner.strip(),
            author=AUTHOR,
        ),
    )


@router.post("/p/{slug}/questions/{qid}/resolve")
def question_resolve(
    slug: str,
    qid: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    resolution: Annotated[str, Form()] = "",
    by_adr: Annotated[str, Form()] = "",
    chosen_option: Annotated[str, Form()] = "",
) -> RedirectResponse:
    session, project_id = db
    return _mutate(
        session,
        slug,
        qid,
        lambda: question_service.resolve(
            session,
            project_id=project_id,
            question_id=qid,
            resolution=resolution.strip() or None,
            by_adr=by_adr.strip() or None,
            chosen_option=int(chosen_option) if chosen_option.strip() else None,
            author=AUTHOR,
        ),
    )


@router.post("/p/{slug}/questions/{qid}/drop")
def question_drop(
    slug: str,
    qid: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    resolution: Annotated[str, Form()] = "",
) -> RedirectResponse:
    session, project_id = db
    return _mutate(
        session,
        slug,
        qid,
        lambda: question_service.drop(
            session,
            project_id=project_id,
            question_id=qid,
            resolution=resolution.strip(),
            author=AUTHOR,
        ),
    )


@router.post("/p/{slug}/questions/{qid}/reopen")
def question_reopen(
    slug: str,
    qid: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> RedirectResponse:
    session, project_id = db
    return _mutate(
        session,
        slug,
        qid,
        lambda: question_service.reopen(
            session, project_id=project_id, question_id=qid, author=AUTHOR
        ),
    )


@router.post("/p/{slug}/questions/{qid}/options")
def question_option_add(
    slug: str,
    qid: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    title: Annotated[str, Form()],
    body: Annotated[str, Form()] = "",
) -> RedirectResponse:
    session, project_id = db
    return _mutate(
        session,
        slug,
        qid,
        lambda: question_service.add_option(
            session,
            project_id=project_id,
            question_id=qid,
            title=title.strip(),
            body=body.strip() or None,
            author=AUTHOR,
        ),
        anchor="options",
    )


@router.post("/p/{slug}/questions/{qid}/options/{position}")
def question_option_change(
    slug: str,
    qid: str,
    position: int,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    op: Annotated[str, Form()] = "update",
    title: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """``op=update`` patches title/body, ``op=delete`` removes the option."""
    session, project_id = db
    if op == "delete":
        return _mutate(
            session,
            slug,
            qid,
            lambda: question_service.remove_option(
                session, project_id=project_id, question_id=qid, position=position, author=AUTHOR
            ),
            anchor="options",
        )
    return _mutate(
        session,
        slug,
        qid,
        lambda: question_service.update_option(
            session,
            project_id=project_id,
            question_id=qid,
            position=position,
            title=title.strip() or None,
            body=body.strip(),
            author=AUTHOR,
        ),
        anchor="options",
    )


@router.post("/p/{slug}/questions/{qid}/links")
def question_link(
    slug: str,
    qid: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    to_kind: Annotated[str, Form()],
    to_ref: Annotated[str, Form()],
    relation: Annotated[str, Form()] = "about",
    note: Annotated[str, Form()] = "",
    op: Annotated[str, Form()] = "attach",
) -> RedirectResponse:
    """``op=attach`` adds the edge and checks it right away; ``op=detach`` removes it."""
    session, project_id = db
    if to_kind not in LINK_KIND_OPTIONS or relation not in RELATION_OPTIONS:
        return _back(slug, qid, f"unknown link kind/relation: {to_kind}/{relation}")
    kind = QuestionLinkKind(to_kind)
    rel = QuestionRelation(relation)
    ref = to_ref.strip()

    def attach() -> None:
        question_service.link(
            session,
            project_id=project_id,
            question_id=qid,
            to_kind=kind,
            to_ref=ref,
            relation=rel,
            note=note.strip() or None,
            author=AUTHOR,
        )
        question_service.verify_links(session, project_id=project_id, question_id=qid)

    def detach() -> None:
        question_service.unlink(
            session,
            project_id=project_id,
            question_id=qid,
            to_kind=kind,
            to_ref=ref,
            relation=rel,
            author=AUTHOR,
        )

    return _mutate(session, slug, qid, detach if op == "detach" else attach, anchor="links")


@router.post("/p/{slug}/questions/{qid}/verify")
def question_verify(
    slug: str,
    qid: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> RedirectResponse:
    session, project_id = db
    return _mutate(
        session,
        slug,
        qid,
        lambda: question_service.verify_links(session, project_id=project_id, question_id=qid),
        anchor="links",
    )
