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

import re
from datetime import UTC, date, datetime
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
from cod_doc.domain.entities import ActorKind, ADRStatus, EntityKind, actor_kind_for_author
from cod_doc.services import (
    adr_health,
    adr_service,
    adr_topic_service,
    revision_service,
    task_service,
)
from cod_doc.services.adr_service import ADRAlreadyExistsError, ADRNotFoundError
from cod_doc.services.adr_topic_service import ADRTopicNotFoundError

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


#: ARG-004: фильтры сводки над списком (``?view=``).
_LIST_VIEWS = frozenset({"pending", "gaps", "replaced"})

#: Знак слева от номера. У действующего знака нет — строка молчит.
_STATUS_GLYPH = {
    "proposed": "!",
    "superseded": "↻",
    "deprecated": "⊘",
    "rejected": "×",
}

#: Класс цвета знака. Полные имена, а не f-строка: их ищет
#: ``test_no_dead_adr_selectors`` — у каждого класса есть правило в CSS.
_GLYPH_CLASS = {
    "proposed": "adr-glyph-proposed",
    "superseded": "adr-glyph-superseded",
    "deprecated": "adr-glyph-deprecated",
    "rejected": "adr-glyph-rejected",
}

#: Находки ``adr_health``, которые список показывает как пробел в записи.
#: ``adr_stale_proposal`` сюда не входит: долгое ожидание уже видно в
#: колонке «когда» и в фильтре «ждут решения».
_GAP_TEXT = {
    "adr_missing_decided_at": "accepted without a decision date",
    "adr_empty_decision": "the Decision section is empty",
    "adr_proposed_has_date": "has a decision date but is not accepted",
    "adr_depends_on_closed": "depends on a decision that is no longer in force",
}


#: Подпись полки для ADR без темы; это не строка в ``adr_topic``.
_NO_TOPIC = "No topic"


def _shelf_sort_key(
    status: str, decided_at: date | None, created: date, adr_id: str
) -> tuple[int, int, str]:
    """ARG-009: порядок внутри полки — действующие (свежие сверху), черновики
    (дольше ждущие сверху), затем снятые, отклонённые и заменённые."""
    if status == "accepted":
        # Без даты — в конец действующих; ordinal со знаком минус даёт «свежие сверху».
        return (0, -decided_at.toordinal() if decided_at else 0, adr_id)
    if status == "proposed":
        return (1, created.toordinal(), adr_id)
    return (2, 0, adr_id)


def _group_by_shelf(
    items: list[dict[str, Any]], topics: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """ARG-009 (RFC 34 §3.4): строки списка, разложенные по полкам.

    Полки — в заданном человеком порядке, «No topic» — последней. Полка без
    видимых строк не показывается: пустая полка в списке шум, а под фильтром
    — тем более. Пока у проекта нет ни одной полки, возвращается одна группа
    без заголовка — список выглядит плоским, как до полок.
    """
    if not topics:
        return [{"name": None, "items": sorted(items, key=lambda it: it["sort"])}]
    by_topic: dict[int | None, list[dict[str, Any]]] = {}
    for it in items:
        by_topic.setdefault(it["topic_id"], []).append(it)
    shelves: list[dict[str, Any]] = [
        {
            "name": t["name"],
            "includes": t["includes"],
            "excludes": t["excludes"],
            "items": sorted(by_topic[t["row_id"]], key=lambda it: it["sort"]),
        }
        for t in topics
        if by_topic.get(t["row_id"])
    ]
    known = {t["row_id"] for t in topics}
    loose = [it for tid, rows in by_topic.items() if tid not in known for it in rows]
    if loose:
        shelves.append(
            {
                "name": _NO_TOPIC,
                "includes": "",
                "excludes": "",
                "items": sorted(loose, key=lambda it: it["sort"]),
                "no_topic": True,
            }
        )
    return shelves


def _row_visible(
    adr_id: str,
    row_status: str,
    *,
    status: str | None,
    view: str | None,
    gaps: dict[str, list[str]],
) -> bool:
    if status:
        return row_status == status
    if view == "pending":
        return row_status == "proposed"
    if view == "gaps":
        return adr_id in gaps
    if view == "replaced":
        return row_status == "superseded"
    # По умолчанию заменённое строкой не показываем: на него ведёт пометка
    # «replaces» у преемника, а в списке оно выглядело бы ещё одним решением.
    return row_status != "superseded"


def _when(status: str, decided_at: date | None, created: date, today: date) -> dict[str, str]:
    """Колонка «когда»: дата решения, срок ожидания черновика или пробел."""
    if status == "proposed":
        days = (today - created).days
        stale = days > adr_health.STALE_PROPOSAL_DAYS
        return {
            "text": f"waiting {days} d",
            "cls": "adr-when-stale" if stale else "adr-when-waiting",
            "title": f"proposed {created.isoformat()}",
        }
    if status == "accepted" and decided_at is None:
        return {
            "text": "no date",
            "cls": "adr-date-missing",
            "title": "Accepted without a decision date — see routine adr_health",
        }
    return {"text": decided_at.isoformat() if decided_at else "—", "cls": "", "title": ""}


def _facts(status: str, created: date, author: str, ref: dict[str, int]) -> list[str]:
    """Строка фактов под заголовком: то, что нужно, чтобы решать, не открывая ADR."""
    facts = []
    if status == "proposed":
        facts.append(f"proposed {created.isoformat()}")
    # Роль автора — только через резолвер ADR-012, не по префиксу строки.
    is_agent = actor_kind_for_author(author) == ActorKind.AGENT
    facts.append("written by an agent" if is_agent else f"by {author}")
    docs, tasks = ref["docs"], ref["tasks"]
    facts.append(
        f"{docs} doc section{'s' if docs != 1 else ''} refer to it" if docs else "no doc references"
    )
    facts.append(f"{tasks} linked task{'s' if tasks != 1 else ''}" if tasks else "no tasks")
    return facts


def _relation_notes(graph: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Пометки связей в строке: «replaces ADR-011», «amends ADR-005», …

    Исходящие связи показываются всегда. Входящие — только от решений, которые
    уже не черновики: у ADR-010 строка не кричит о том, что ADR-016 ещё
    только собирается его уточнить (это видно на карточке). Входящая
    ``depends_on`` на строке не показывается вовсе — порядок принятия важен
    тому, кто опирается, а не тому, на кого.
    """
    status = {n["adr_id"]: n["status"] for n in graph["nodes"]}
    out: dict[str, list[dict[str, str]]] = {}

    def add(adr_id: str, label: str, target: str) -> None:
        out.setdefault(adr_id, []).append({"label": label, "adr_id": target})

    for e in graph["edges"]:
        add(e["from"], "replaces", e["to"])
        add(e["to"], "replaced by", e["from"])
    for rel in graph["relations"]:
        src, dst, kind = rel["from"], rel["to"], rel["kind"]
        proposed = status.get(src) == "proposed"
        if kind == "amends":
            add(src, "will amend" if proposed else "amends", dst)
            if not proposed:
                add(dst, "amended by", src)
        else:
            add(src, "depends on", dst)
    return out


#: ARG-006: стрелка mermaid на каждый вид связи из ``adr_relation``.
_GRAPH_RELATION_ARROW = {
    "amends": "-.->|amends|",
    "depends_on": "==>|depends on|",
}


#: ARG-005: разделы тела, полноту которых карточка показывает перед решением.
_RECORD_FIELDS: tuple[tuple[str, str], ...] = (
    ("context", "Context"),
    ("decision", "Decision"),
    ("alternatives", "Alternatives"),
    ("consequences", "Consequences"),
)

#: Сколько символов названия альтернативы показывать в блоке «Rejected».
_ALT_TITLE_MAX = 90

_ALT_ENUM = re.compile(r"^\w{1,2}[.)]\s+")
_ALT_LEAD = re.compile(r"^(?:\*\*(?P<bold>.+?)\*\*|(?:\d+[.)]|[-*])\s+(?P<item>.+))")


def _alternative_titles(text: str | None) -> list[str]:
    """Названия отвергнутых вариантов: заголовки, жирные начала абзацев, пункты.

    Тела ADR пишут альтернативы по-разному: ``### A. …`` заголовком,
    ``**A. …** Отвергнуто: …`` абзацем (ADR-016), ``1. Mem0 — …`` списком
    (ADR-009). Для блока «Rejected» нужно только название — до первого
    « — » или точки с пробелом.
    """
    if not text:
        return []
    heads = [anchor_title for anchor_title, _ in outline(text)]
    if heads:
        return [re.sub(r"<[^>]+>", "", h) for h in heads]
    titles: list[str] = []
    for line in text.splitlines():
        m = _ALT_LEAD.match(line.strip())
        if not m:
            continue
        lead = (m.group("bold") or m.group("item") or "").strip()
        # «A. Общая PostgreSQL» — перечислитель срезаем до разреза по «. »,
        # иначе от названия оставалась одна буква.
        lead = _ALT_ENUM.sub("", lead)
        lead = re.split(r" — |: |\. ", lead, maxsplit=1)[0].rstrip(".")
        if len(lead) > _ALT_TITLE_MAX:
            lead = lead[: _ALT_TITLE_MAX - 1] + "…"
        titles.append(lead)
    return titles


def _decision_effects(rels: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """«Что изменится при принятии» — по связям черновика (ARG-005).

    Каждая запись — ``{adr_id, title, text, warn}``; ``warn`` — то, что
    мешает принять сейчас или требует пересмотра.
    """
    effects: list[dict[str, Any]] = []
    for r in rels.get("outgoing", []):
        if r["kind"] == "amends":
            effects.append(
                {
                    **r,
                    "text": "gets “amended by” this ADR; both stay in force",
                    "warn": r["status"] in _CLOSED_STATUSES,
                }
            )
        elif r["status"] == "accepted":
            effects.append({**r, "text": "this ADR relies on it — it is in force", "warn": False})
        elif r["status"] == "proposed":
            effects.append(
                {**r, "text": "is not accepted yet — accept it first or together", "warn": True}
            )
        else:
            effects.append(
                {**r, "text": f"is {r['status']} — revisit this dependency", "warn": True}
            )
    for r in rels.get("incoming", []):
        if r["kind"] == "depends_on":
            effects.append(
                {
                    **r,
                    "text": "depends on this ADR — it can be accepted after this one",
                    "warn": False,
                }
            )
    return effects


def _record_completeness(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"name": name, "ok": bool((payload.get(key) or "").strip())} for key, name in _RECORD_FIELDS
    ]


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
    view: str | None = None,
) -> HTMLResponse:
    """ARG-004 (RFC 34 §3.2): список отвечает «что мне решать» и «на что опереться».

    Действующее решение в строке молчит: номер, заголовок, дата. Сигналы —
    только у строк, которым нужно действие: черновик несёт срок ожидания и
    факты (автор, кто ссылается, задачи), запись с пробелом — сам пробел.
    Пробелы берутся из ``adr_health.assess`` — те же правила, что у рутины,
    а не второй список условий в шаблоне.

    ``view`` — фильтры сводки: ``pending`` (ждут решения), ``gaps`` (пробелы
    в записи), ``replaced`` (заменённые; по умолчанию строкой не
    показываются — на них ведёт пометка у преемника). ``status`` оставлен для
    старых ссылок.
    """
    proj = get_project(slug)
    session, project_id = db
    status = status or None
    view = view if view in _LIST_VIEWS else None
    all_rows = adr_service.list_for_project(session, project_id)
    graph = adr_service.graph(session, project_id)
    notes = _relation_notes(graph)
    refs = adr_service.reference_counts(session, project_id)
    gaps: dict[str, list[str]] = {}
    for issue in adr_health.assess(session, project_id):
        if issue.code in _GAP_TEXT:
            adr_id = issue.scope_id.split("->", 1)[0]
            gaps.setdefault(adr_id, []).append(_GAP_TEXT[issue.code])
    today = datetime.now(UTC).date()

    counts = {s: 0 for s in STATUS_OPTIONS}
    for r in all_rows:
        counts[r.status] = counts.get(r.status, 0) + 1

    items = []
    for r in all_rows:
        if not _row_visible(r.adr_id, r.status, status=status, view=view, gaps=gaps):
            continue
        pending = r.status == "proposed"
        row_gaps = gaps.get(r.adr_id, [])
        ref = refs.get(r.adr_id, {"docs": 0, "tasks": 0})
        items.append(
            {
                "adr_id": r.adr_id,
                "title": r.title,
                "status": r.status,
                "glyph": _STATUS_GLYPH.get(r.status, ""),
                "glyph_cls": _GLYPH_CLASS.get(r.status, ""),
                "author": r.author,
                "closed": r.status in _CLOSED_STATUSES,
                "notes": notes.get(r.adr_id, []),
                "when": _when(r.status, r.decided_at, r.created.date(), today),
                "facts": _facts(r.status, r.created.date(), r.author, ref)
                if pending or row_gaps
                else [],
                "gaps": row_gaps,
                "topic_id": r.topic_id,
                "sort": _shelf_sort_key(r.status, r.decided_at, r.created.date(), r.adr_id),
            }
        )
    # Словари, а не ORM-модели: веб-слой не импортирует infra (test_web_layer_imports).
    topics = [
        {"row_id": t.row_id, **adr_topic_service.topic_to_dict(t)}
        for t in adr_topic_service.list_for_project(session, project_id)
    ]

    return templates.TemplateResponse(
        request,
        "project/adr_list.html",
        {
            "project": proj.entry,
            "items": items,
            "shelves": _group_by_shelf(items, topics),
            "status_filter": status,
            "view": view,
            "summary": {
                "total": len(all_rows),
                "in_force": counts.get("accepted", 0),
                "pending": counts.get("proposed", 0),
                "gaps": len(gaps),
                "replaced": counts.get("superseded", 0),
                "withdrawn": counts.get("deprecated", 0) + counts.get("rejected", 0),
            },
        },
    )


@router.get("/p/{slug}/adr/new", response_class=HTMLResponse)
def adr_new_form(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    amends: str | None = None,
) -> HTMLResponse:
    """ADR-005: empty form to create a new ADR.

    ARG-005: ``?amends=ADR-NNN`` — «уточнить новым ADR» с карточки: новое
    решение сразу получит связь ``amends`` на исходное.
    """
    proj = get_project(slug)
    session, project_id = db
    amended = adr_service.get(session, project_id, amends) if amends else None
    return templates.TemplateResponse(
        request,
        "project/adr_new.html",
        {
            "project": proj.entry,
            "form_action": f"/p/{slug}/adr/new",
            "status_options": STATUS_OPTIONS,
            "default_status": "proposed",
            "next_id": adr_service.next_adr_id(session, project_id),
            "amends": {"adr_id": amended.adr_id, "title": amended.title} if amended else None,
            "topics": adr_topic_service.names(session, project_id),
            # «Уточнить новым ADR» — по умолчанию на ту же полку, что исходное решение.
            "default_topic": adr_service.topic_name(session, amended) if amended else None,
        },
    )


@router.post("/p/{slug}/adr/preview", response_class=HTMLResponse)
def adr_preview(
    request: Request,
    slug: str,
    context: Annotated[str | None, Form()] = None,
    decision: Annotated[str | None, Form()] = None,
    alternatives: Annotated[str | None, Form()] = None,
    consequences: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """ADO-235: markdown тела так, как его отрисует карточка, — без записи.

    Тот же ``_render_prose`` и те же заголовки, что у ``adr_show``: иначе
    предпросмотр показывал бы не то, что сохранится.
    """
    get_project(slug)
    fields = {
        "context": context,
        "decision": decision,
        "alternatives": alternatives,
        "consequences": consequences,
    }
    sections = [
        {"anchor": anchor, "title": title, "html": _render_prose(fields[key], slug)}
        for key, anchor, title in _BODY_FIELDS
        if (fields[key] or "").strip()
    ]
    return templates.TemplateResponse(request, "_frag/adr_preview.html", {"sections": sections})


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
    amends: Annotated[str | None, Form()] = None,
    topic: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Create the ADR and redirect to its detail page.

    ARG-005: ``amends`` — создать и тут же записать связь «уточняет» одной
    транзакцией: решение без связи потеряло бы, ради чего его завели.
    """
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
        shelf = (topic or "").strip()
        if shelf:
            adr_service.set_topic(
                session,
                project_id=project_id,
                adr_id=row.adr_id,
                topic=shelf,
                author="human:web",
            )
        if amends:
            adr_service.relate(
                session,
                project_id=project_id,
                from_adr_id=row.adr_id,
                to_adr_id=amends,
                kind="amends",
                author="human:web",
            )
        session.commit()
    except ADRAlreadyExistsError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ADRNotFoundError, ADRTopicNotFoundError) as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    """ADR-006: граф решений — замены и связи (ARG-006) одной mermaid-схемой."""
    proj = get_project(slug)
    session, project_id = db
    graph = adr_service.graph(session, project_id)

    # В mermaid идут только узлы с рёбрами. Весь реестр целиком давал граф
    # из десятка несвязанных прямоугольников, среди которых единственная
    # стрелка терялась; одиночные решения перечислены списком под графом.
    # ARG-006: связи «уточняет» / «опирается на» рисуются вместе с заменами,
    # их узлы тоже уходят из «Standalone».
    every_edge = [*graph["edges"], *graph["relations"]]
    linked = {e["from"] for e in every_edge} | {e["to"] for e in every_edge}
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
    # Вид связи — начертание стрелки: пунктир «уточняет» (оба действуют),
    # толстая «опирается на» (порядок принятия). Легенда — в шаблоне.
    for rel in graph["relations"]:
        arrow = _GRAPH_RELATION_ARROW[rel["kind"]]
        lines.append(f"  {rel['from'].replace('-', '_')} {arrow} {rel['to'].replace('-', '_')}")
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
            "edge_count": len(every_edge),
            "standalone": standalone,
            "status_icons": _STATUS_ICON,
        },
    )


@router.get("/p/{slug}/adr/shelves", response_class=HTMLResponse)
def adr_shelves_page(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """ARG-010 (RFC 34 §3.4): полками управляют здесь, а список только читают.

    Создание, правка состава, порядок и удаление; пустые полки видны только
    на этой странице. «No topic» — не полка, а счётчик внизу.
    """
    proj = get_project(slug)
    session, project_id = db
    topics = adr_topic_service.list_for_project(session, project_id)
    counts = adr_topic_service.adr_counts(session, project_id)
    loose = adr_topic_service.loose_count(session, project_id)
    return templates.TemplateResponse(
        request,
        "project/adr_shelves.html",
        {
            "project": proj.entry,
            "shelves": [
                adr_topic_service.topic_to_dict(t, adr_count=counts.get(t.row_id, 0))
                for t in topics
            ],
            "loose": loose,
        },
    )


@router.post("/p/{slug}/adr/shelves")
def adr_shelves_submit(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    action: Annotated[str, Form()],
    name: Annotated[str, Form()],
    new_name: Annotated[str | None, Form()] = None,
    includes: Annotated[str | None, Form()] = None,
    excludes: Annotated[str | None, Form()] = None,
    position: Annotated[int | None, Form()] = None,
) -> RedirectResponse:
    """ARG-010: одна точка записи полок — ``action`` ∈ create/update/move/delete.

    Один POST, а не ``/adr/shelves/<name>/…``: имя полки — кириллица с
    пробелами, а путь вида ``/adr/shelves/edit`` перехватил бы роут
    ``/adr/{adr_id}/edit``.
    """
    session, project_id = db
    author = "human:web"
    try:
        if action == "create":
            adr_topic_service.create(
                session,
                project_id=project_id,
                name=name,
                includes=includes or "",
                excludes=excludes or "",
                author=author,
            )
        elif action == "update":
            adr_topic_service.update(
                session,
                project_id=project_id,
                name=name,
                new_name=new_name,
                includes=includes,
                excludes=excludes,
                author=author,
            )
        elif action == "move" and position is not None:
            adr_topic_service.move(
                session, project_id=project_id, name=name, position=position, author=author
            )
        elif action == "delete":
            adr_topic_service.delete(session, project_id=project_id, name=name, author=author)
        else:
            raise HTTPException(status_code=400, detail="unknown shelf action")
        session.commit()
    except ADRTopicNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/shelves", status_code=303)


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
    all_rows = adr_service.list_for_project(session, project_id)
    candidates = [
        {"adr_id": r.adr_id, "title": r.title, "status": r.status}
        for r in all_rows
        if r.adr_id != adr_id and r.status not in _CLOSED_STATUSES
    ]

    # ARG-005: связи «уточняет» / «опирается на» и то, что нужно для решения.
    rels = payload.get("relations") or {"outgoing": [], "incoming": []}
    today = datetime.now(UTC).date()
    created = {r.adr_id: r.created.date() for r in all_rows}
    proposed_amendments = [
        {**r, "waiting": (today - created[r["adr_id"]]).days if r["adr_id"] in created else None}
        for r in rels["incoming"]
        if r["kind"] == "amends" and r["status"] == "proposed"
    ]
    record = _record_completeness(payload)
    refs = payload.get("referenced_by") or {"docs": [], "tasks": [], "adrs": []}
    decide = None
    if payload["status"] == "proposed":
        decide = {
            "waiting": (today - row.created.date()).days,
            "effects": _decision_effects(rels),
            "rejected": _alternative_titles(payload.get("alternatives")),
            "doc_sections": len(refs["docs"]),
            "doc_count": len({d["doc_key"] for d in refs["docs"]}),
            "today": today.isoformat(),
        }

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
            "relations": rels,
            "proposed_amendments": proposed_amendments,
            "record": record,
            "record_gaps": [f["name"] for f in record if not f["ok"]],
            "decide": decide,
            "author_is_agent": actor_kind_for_author(payload["author"]) == ActorKind.AGENT,
            "topics": adr_topic_service.names(session, project_id),
        },
    )


@router.post("/p/{slug}/adr/{adr_id}/topic")
def adr_set_topic_form(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    topic: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """ARG-010: выбрать полку с карточки — в любом статусе; пусто — «No topic»."""
    session, project_id = db
    try:
        adr_service.set_topic(
            session,
            project_id=project_id,
            adr_id=adr_id,
            topic=(topic or "").strip() or None,
            author="human:web",
        )
        session.commit()
    except (ADRNotFoundError, ADRTopicNotFoundError) as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{adr_id}", status_code=303)


@router.post("/p/{slug}/adr/{adr_id}/accept")
def adr_accept(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    decided_at: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """ARG-005: принять черновик с карточки. Без даты сервис поставит сегодняшнюю (ARG-002)."""
    return _decide(slug, adr_id, db, status="accepted", decided_at=_parse_date(decided_at))


@router.post("/p/{slug}/adr/{adr_id}/reject")
def adr_reject(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """ARG-005: отклонить черновик с карточки; причина уходит в ревизию."""
    return _decide(slug, adr_id, db, status="rejected", reason=(reason or "").strip() or None)


def _decide(
    slug: str,
    adr_id: str,
    db: tuple[Session, int],
    *,
    status: str,
    decided_at: date | None = None,
    reason: str | None = None,
) -> RedirectResponse:
    session, project_id = db
    row = adr_service.get(session, project_id, adr_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"ADR {adr_id} not found")
    if row.status != "proposed":
        # Повторная отправка формы с устаревшей страницы — не ошибка сервера.
        raise HTTPException(status_code=409, detail=f"ADR {adr_id} is {row.status}, not proposed")
    try:
        adr_service.update(
            session,
            project_id=project_id,
            adr_id=adr_id,
            status=status,
            decided_at=decided_at,
            author="human:web",
            reason=reason or f"{status} via web",
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{adr_id}", status_code=303)


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
