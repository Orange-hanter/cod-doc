"""WUI-006: палитра поиска — ``Cmd/Ctrl+K`` с любой страницы.

Один роут отдаёт HTML-фрагмент результатов; оболочка (``<dialog>``) живёт в
``base.html``, клавиатуру ведёт ``static/cod_doc_palette.js``. Источники:

- в проекте — переходы на вкладки проекта (подстрока в названии) и хиты FTS
  (``search_service.search``: тот же префиксный матч, что у страницы
  ``/search``, но по несколько хитов на вид и без находок — им некуда вести);
- вне проекта (главная, стандарты, настройки) — проекты реестра по
  подстроке в слаге: искать по содержимому там негде.

Нет FTS-индекса (БД старше миграции 0023) — строка с подсказкой вместо 500,
как на ``/search`` (CUR-010). Ничего не пишет.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.deps import get_config, try_open_project_db
from cod_doc.api.web.navigation import hit_url, project_tabs
from cod_doc.api.web.snippets import fts_snippet
from cod_doc.api.web.templates_env import templates
from cod_doc.services import search_service

router = APIRouter()

#: Хитов на вид: палитра — быстрый переход, полный список — на ``/search``.
PALETTE_HITS_PER_KIND: Final = 5
#: Проектов в режиме «вне проекта».
PALETTE_PROJECT_LIMIT: Final = 20
#: Порядок видов в выдаче: то, куда переходят чаще, — выше. Находок нет:
#: у них нет своей страницы, а палитра — это переход (их ищут на ``/search``).
_KIND_ORDER: Final[tuple[str, ...]] = ("task", "doc", "story", "adr", "question")
_KIND_LABEL: Final[dict[str, str]] = {
    "task": "Tasks",
    "doc": "Docs",
    "story": "Stories",
    "adr": "ADRs",
    "question": "Questions",
}


def _matches(needle: str, *haystacks: str) -> bool:
    return not needle or any(needle in h.casefold() for h in haystacks)


def _project_groups(q: str) -> list[dict[str, Any]]:
    needle = q.strip().casefold()
    names = [e.name for e in get_config().list_projects() if _matches(needle, e.name)]
    items = [
        {"href": f"/p/{name}", "title": name, "ref": "", "snippet": ""}
        for name in names[:PALETTE_PROJECT_LIMIT]
    ]
    return [{"label": "Projects", "items": items}] if items else []


def _tab_group(slug: str, q: str) -> dict[str, Any] | None:
    needle = q.strip().casefold()
    items = [
        {"href": href, "title": label, "ref": "", "snippet": ""}
        for key, label, href in project_tabs(slug)
        if _matches(needle, label, key)
    ]
    return {"label": "Go to", "items": items} if items else None


def _search_groups(slug: str, q: str) -> tuple[list[dict[str, Any]], str | None]:
    """Хиты FTS по видам и сообщение об ошибке индекса (или ``None``)."""
    if not q.strip():
        return [], None
    with try_open_project_db(slug) as (session, project_id):
        if session is None or project_id is None:
            return [], None
        try:
            result = search_service.search(
                session, project_id=project_id, query=q, limit=PALETTE_HITS_PER_KIND
            )
        except search_service.SearchIndexMissing as exc:
            return [], str(exc)
    groups = []
    for kind in _KIND_ORDER:
        hits = result["by_kind"].get(kind) or []
        items = [
            {
                "href": hit_url(slug, kind, h["ref"]),
                "title": h["title"],
                "ref": h["ref"],
                "snippet": fts_snippet(h["snippet"]),
                "kind": kind,
            }
            for h in hits
        ]
        if items:
            groups.append({"label": _KIND_LABEL[kind], "items": items})
    return groups, None


@router.get("/palette", response_class=HTMLResponse)
def palette(request: Request, q: str = "", project: str = "") -> HTMLResponse:
    """Фрагмент результатов палитры для ``q`` в контексте ``project``."""
    slug = project.strip()
    entry = get_config().get_project(slug) if slug else None
    groups: list[dict[str, Any]] = []
    index_error: str | None = None
    if entry is None:
        slug = ""
        groups = _project_groups(q)
    else:
        found, index_error = _search_groups(slug, q)
        tabs = _tab_group(slug, q)
        # Пустой запрос — только переходы; с запросом — сначала содержимое:
        # ради него палитру и открывают, а вкладки видны в полосе и так.
        ordered = [tabs, *found] if not q.strip() else [*found, tabs]
        groups = [g for g in ordered if g is not None]
    return templates.TemplateResponse(
        request,
        "_frag/palette_results.html",
        {
            "q": q,
            "slug": slug,
            "groups": groups,
            "index_error": index_error,
        },
    )
