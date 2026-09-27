"""ADR-004/005/006: web pages for Architecture Decision Records.

Routes:
- ``GET /p/{slug}/adr``                 — list with status filter (ADR-004).
- ``GET /p/{slug}/adr/new``             — create form (ADR-005).
- ``POST /p/{slug}/adr/new``            — submit; redirect to detail.
- ``GET /p/{slug}/adr/graph``           — supersede DAG (ADR-006).
- ``GET /p/{slug}/adr/{adr_id}``        — detail view (ADR-005).
- ``POST /p/{slug}/adr/{adr_id}/edit``  — patch fields; redirect to detail.
- ``POST /p/{slug}/adr/{adr_id}/diagram`` — attach a Mermaid diagram.
- ``POST /p/{slug}/adr/{adr_id}/supersede`` — record replaces edge.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.markdown import (
    autolink_adr_refs,
    lead_paragraph,
    outline,
    render_markdown,
)
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import ADRStatus, EntityKind
from cod_doc.services import adr_service, revision_service, task_service
from cod_doc.services.adr_service import ADRAlreadyExistsError, ADRNotFoundError

router = APIRouter()


#: Единственный источник списка статусов для форм и фильтра.
#: До ADO-133 тот же литерал из пяти строк был выписан трижды — в списке,
#: в форме создания и на детальной странице; новый член ``ADRStatus``
#: пришлось бы добавлять в каждое место руками.
STATUS_OPTIONS: list[str] = [s.value for s in ADRStatus]

_STATUS_ICON = {
    "proposed": "✏️",
    "accepted": "✅",
    "superseded": "🔁",
    "deprecated": "⚠️",
    "rejected": "❌",
}


#: Статусы, после которых решение больше не действует. Строки таких ADR в
#: списке приглушены, а карточка открывается баннером «не действует».
_CLOSED_STATUSES = frozenset({"superseded", "deprecated", "rejected"})

#: Тело ADR в порядке чтения: ключ поля, якорь, заголовок. Один источник и
#: для секций статьи, и для оглавления в сайдбаре.
_BODY_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("context", "context", "Context"),
    ("decision", "decision", "Decision"),
    ("alternatives", "alternatives", "Alternatives considered"),
    ("consequences", "consequences", "Consequences"),
)


def _relations(graph: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Рёбра supersede-DAG, разложенные по узлу: кого заменяет и кем заменён.

    Без этого связь видна только на странице графа или из прозы тела:
    ADR-011 в списке выглядел действующим наравне с заменившим его ADR-012.
    """
    titles = {n["adr_id"]: n["title"] for n in graph["nodes"]}
    out: dict[str, dict[str, list[dict[str, Any]]]] = {
        n["adr_id"]: {"supersedes": [], "superseded_by": []} for n in graph["nodes"]
    }
    for e in graph["edges"]:
        new, old = e["from"], e["to"]
        if new in out:
            out[new]["supersedes"].append(
                {"adr_id": old, "title": titles.get(old, ""), "reason": e["reason"]}
            )
        if old in out:
            out[old]["superseded_by"].append(
                {"adr_id": new, "title": titles.get(new, ""), "reason": e["reason"]}
            )
    return out


#: Длина подписи узла в графе: длиннее — mermaid растягивает прямоугольник
#: на полэкрана.
_GRAPH_TITLE_MAX = 40

#: Заливка узлов графа по статусу ADR. Mermaid рисует SVG вне нашего CSS,
#: поэтому цвета здесь литералами; тон — как у ``.badge-*``.
_GRAPH_CLASSDEF = {
    "proposed": "fill:#fef3c7,stroke:#d97706,color:#78350f",
    "accepted": "fill:#dcfce7,stroke:#16a34a,color:#14532d",
    "superseded": "fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-dasharray:4 3",
    "deprecated": "fill:#f1f5f9,stroke:#94a3b8,color:#475569,stroke-dasharray:4 3",
    "rejected": "fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-dasharray:4 3",
}


def _render_prose(text: str | None, slug: str) -> str:
    """Markdown-render an ADR body field and autolink bare ``ADR-NNN`` refs."""
    if not text:
        return ""
    return autolink_adr_refs(render_markdown(text), slug=slug)


def _decision_brief(decision: str | None, slug: str) -> dict[str, Any] | None:
    """ADO-229: суть решения для начала карточки, без нового поля в модели.

    Заголовки внутри Decision — это и есть пункты решения (ADR-012: «1.
    audit_log — снять контракт…»), их список короче любого абзаца. Нет
    заголовков — первый абзац. Пустой Decision — карточки нет вовсе.
    """
    if not decision:
        return None
    # Пункт — сам ссылка на свой заголовок; автоссылка ADR-NNN внутри дала
    # бы вложенный <a>.
    points = [{"html": html, "anchor": anchor} for html, anchor in outline(decision)]
    if points:
        return {"points": points, "lead": ""}
    lead = lead_paragraph(decision)
    if not lead:
        return None
    return {"points": [], "lead": autolink_adr_refs(lead, slug=slug)}


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


@router.get("/p/{slug}/adr", response_class=HTMLResponse)
def adr_list(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    status: str | None = None,
) -> HTMLResponse:
    """ADR-004: list page with status filter + badges."""
    proj = get_project(slug)
    session, project_id = db
    status = status or None
    all_rows = adr_service.list_for_project(session, project_id)
    relations = _relations(adr_service.graph(session, project_id))

    # Фильтр — ссылки-чипы со счётчиками, а не <select>: без JS работает
    # сам по себе, а число рядом со статусом отвечает «сколько решений ещё
    # не принято» без перехода. Пустые статусы не показываем, кроме
    # выбранного — иначе с него не уйти.
    counts = {s: 0 for s in STATUS_OPTIONS}
    for r in all_rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    status_chips = [
        {"status": s, "count": counts[s], "icon": _STATUS_ICON.get(s, "•")}
        for s in STATUS_OPTIONS
        if counts[s] or s == status
    ]

    items = []
    for r in all_rows:
        if status and r.status != status:
            continue
        rel = relations.get(r.adr_id, {"supersedes": [], "superseded_by": []})
        items.append(
            {
                "adr_id": r.adr_id,
                "title": r.title,
                "status": r.status,
                "status_icon": _STATUS_ICON.get(r.status, "•"),
                "decided_at": r.decided_at,
                "author": r.author,
                "closed": r.status in _CLOSED_STATUSES,
                "supersedes": rel["supersedes"],
                "superseded_by": rel["superseded_by"],
            }
        )

    return templates.TemplateResponse(
        request,
        "project/adr_list.html",
        {
            "project": proj.entry,
            "items": items,
            "status_filter": status,
            "status_chips": status_chips,
            "total": len(all_rows),
        },
    )


@router.get("/p/{slug}/adr/new", response_class=HTMLResponse)
def adr_new_form(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """ADR-005: empty form to create a new ADR."""
    proj = get_project(slug)
    return templates.TemplateResponse(
        request,
        "project/adr_new.html",
        {
            "project": proj.entry,
            "form_action": f"/p/{slug}/adr/new",
            "status_options": STATUS_OPTIONS,
            "default_status": "proposed",
        },
    )


@router.post("/p/{slug}/adr/new")
def adr_new_submit(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    title: Annotated[str, Form()],
    status: Annotated[str, Form()] = "proposed",
    decided_at: Annotated[str | None, Form()] = None,
    context: Annotated[str | None, Form()] = None,
    decision: Annotated[str | None, Form()] = None,
    alternatives: Annotated[str | None, Form()] = None,
    consequences: Annotated[str | None, Form()] = None,
    adr_id: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Create the ADR and redirect to its detail page."""
    session, project_id = db
    try:
        row = adr_service.create(
            session,
            project_id=project_id,
            title=title.strip(),
            status=status,
            decided_at=_parse_date(decided_at),
            context=(context or None),
            decision=(decision or None),
            alternatives=(alternatives or None),
            consequences=(consequences or None),
            adr_id=(adr_id.strip() if adr_id else None),
            author="human:web",
        )
        session.commit()
    except ADRAlreadyExistsError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{row.adr_id}", status_code=303)


@router.get("/p/{slug}/adr/graph", response_class=HTMLResponse)
def adr_graph_page(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """ADR-006: full supersede DAG rendered as a Mermaid block."""
    proj = get_project(slug)
    session, project_id = db
    graph = adr_service.graph(session, project_id)

    # В mermaid идут только узлы с рёбрами. Весь реестр целиком давал граф
    # из десятка несвязанных прямоугольников, среди которых единственная
    # стрелка терялась; одиночные решения перечислены списком под графом.
    linked = {e["from"] for e in graph["edges"]} | {e["to"] for e in graph["edges"]}
    chained = [n for n in graph["nodes"] if n["adr_id"] in linked]
    standalone = [n for n in graph["nodes"] if n["adr_id"] not in linked]

    lines = ["graph LR"]
    for node in chained:
        node_id = node["adr_id"].replace("-", "_")
        # Mermaid label: id + title; keep it short.
        title = node["title"].replace('"', "'").replace("\n", " ")
        if len(title) > _GRAPH_TITLE_MAX:
            title = title[: _GRAPH_TITLE_MAX - 3] + "…"
        lines.append(f'  {node_id}["{node["adr_id"]}<br/>{title}"]:::{node["status"]}')
    for edge in graph["edges"]:
        from_id = edge["from"].replace("-", "_")
        to_id = edge["to"].replace("-", "_")
        lines.append(f"  {from_id} -->|replaces| {to_id}")
    # Цвет узла = статус; палитра повторяет бейджи списка.
    lines.extend(f"  classDef {status} {style}" for status, style in _GRAPH_CLASSDEF.items())
    mermaid_src = "\n".join(lines)

    return templates.TemplateResponse(
        request,
        "project/adr_graph.html",
        {
            "project": proj.entry,
            "graph": graph,
            "mermaid": mermaid_src,
            "has_nodes": bool(graph["nodes"]),
            "chained": chained,
            "standalone": standalone,
            "status_icons": _STATUS_ICON,
        },
    )


@router.get("/p/{slug}/adr/{adr_id}", response_class=HTMLResponse)
def adr_show(
    request: Request,
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """ADR-005: detail page with diagrams + task links + edit form."""
    proj = get_project(slug)
    session, project_id = db
    row = adr_service.get(session, project_id, adr_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"ADR {adr_id} not found")
    payload = adr_service.adr_to_dict(session, row, include_backlinks=True)
    payload["status_icon"] = _STATUS_ICON.get(payload["status"], "•")
    payload["closed"] = payload["status"] in _CLOSED_STATUSES
    sections = [
        {"anchor": anchor, "title": title, "html": _render_prose(payload.get(key), slug)}
        for key, anchor, title in _BODY_FIELDS
        if payload.get(key)
    ]
    rel = _relations(adr_service.graph(session, project_id)).get(
        adr_id, {"supersedes": [], "superseded_by": []}
    )

    # ADO-231: каждая запись ADR пишет revision; страница ревизий по сущности
    # уже есть, с карточки на неё просто не было входа.
    history = revision_service.list_for_entity(session, EntityKind.ADR, row.row_id)
    last_rev = history[-1] if history else None
    history_info = {
        "count": len(history),
        "url": f"/p/{slug}/revisions?entity_kind={EntityKind.ADR.value}&entity_id={row.row_id}",
        "last_at": last_rev.at if last_rev else None,
        "last_author": last_rev.author if last_rev else None,
    }

    # Голый task_id ничего не говорит о связи: без названия и статуса
    # приходилось открывать каждую задачу. Удалённая задача остаётся в
    # списке — ссылка из ADR на неё сама по себе факт.
    task_links = []
    for link in payload["task_links"]:
        task = task_service.get(session, link["task_id"])
        task_links.append(
            {
                **link,
                "title": task.title if task else None,
                "status": str(task.status) if task else None,
            }
        )

    # Кандидаты на замену — только действующие решения: заменить уже
    # заменённый, отозванный или отклонённый ADR нечего.
    candidates = [
        {"adr_id": r.adr_id, "title": r.title, "status": r.status}
        for r in adr_service.list_for_project(session, project_id)
        if r.adr_id != adr_id and r.status not in _CLOSED_STATUSES
    ]

    return templates.TemplateResponse(
        request,
        "project/adr_show.html",
        {
            "project": proj.entry,
            "adr": payload,
            "sections": sections,
            "brief": _decision_brief(payload.get("decision"), slug),
            "supersedes": rel["supersedes"],
            "superseded_by": rel["superseded_by"],
            "task_links": task_links,
            "history": history_info,
            "candidates": candidates,
            "status_options": STATUS_OPTIONS,
        },
    )


@router.post("/p/{slug}/adr/{adr_id}/edit")
def adr_edit(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    title: Annotated[str, Form()],
    status: Annotated[str, Form()],
    decided_at: Annotated[str | None, Form()] = None,
    context: Annotated[str | None, Form()] = None,
    decision: Annotated[str | None, Form()] = None,
    alternatives: Annotated[str | None, Form()] = None,
    consequences: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    session, project_id = db
    try:
        adr_service.update(
            session,
            project_id=project_id,
            adr_id=adr_id,
            title=title.strip(),
            status=status,
            decided_at=_parse_date(decided_at),
            context=(context or None),
            decision=(decision or None),
            alternatives=(alternatives or None),
            consequences=(consequences or None),
            author="human:web",
        )
        session.commit()
    except ADRNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{adr_id}", status_code=303)


@router.post("/p/{slug}/adr/{adr_id}/diagram")
def adr_add_diagram(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    mermaid: Annotated[str, Form()],
    title: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    session, project_id = db
    try:
        adr_service.add_diagram(
            session,
            project_id=project_id,
            adr_id=adr_id,
            mermaid=mermaid,
            title=(title or None),
            author="human:web",
        )
        session.commit()
    except ADRNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{adr_id}", status_code=303)


@router.post("/p/{slug}/adr/{adr_id}/supersede")
def adr_supersede_post(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    superseded_adr_id: Annotated[str, Form()],
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """``adr_id`` is the *new* ADR that supersedes ``superseded_adr_id``."""
    session, project_id = db
    try:
        adr_service.supersede(
            session,
            project_id=project_id,
            superseding_adr_id=adr_id,
            superseded_adr_id=superseded_adr_id,
            reason=(reason or None),
            author="human:web",
        )
        session.commit()
    except ADRNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{adr_id}", status_code=303)


@router.post("/p/{slug}/adr/{adr_id}/deprecate")
def adr_deprecate_post(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Transition an ADR (PROPOSED or ACCEPTED) to DEPRECATED."""
    session, project_id = db
    try:
        adr_service.deprecate(
            session,
            project_id=project_id,
            adr_id=adr_id,
            reason=(reason or None),
            author="human:web",
        )
        session.commit()
    except ADRNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{adr_id}", status_code=303)
