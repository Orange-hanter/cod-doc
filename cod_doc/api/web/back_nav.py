"""Полоса «назад» на страницах сущностей — ADO-149.

Каждая show-страница (задача, история, документ, ADR, план) получает липкую
полосу под топбаром: ← к родителю и, где у сущности есть упорядоченные
соседи, ‹ prev / next ›.

Родитель — с контекстом, откуда пришли:

1. `?from=<путь>` — явный источник (его ставит, например, «Open full page»
   шторки: под шторкой был список с фильтрами);
2. `Referer` — страница этого же проекта на этом же хосте, кроме самой себя;
3. индекс вкладки — deep link, закладка, переход из другого проекта.

Источник принимается только как путь внутри `/p/<slug>`: ни схемы, ни
хоста, ни `//` — `from` из адресной строки не уводит за пределы приложения.
`history.back()` — лишь улучшение в браузере (`data-back` в
`cod_doc_app.js`), когда предыдущая запись истории и есть этот родитель:
так сохраняется прокрутка списка. Ссылка работает и без него.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlencode, urlsplit

from cod_doc.api.web.errors import same_site_path
from cod_doc.services import plan_service

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import Request
    from sqlalchemy.orm import Session

FROM_PARAM = "from"
# (вкладка, ключ из адреса) → подпись или None.
EntityLabel = Callable[[str, str], str | None]

# Подписи вкладок — те же, что в `_layout/project_tabs.html`.
_TAB_LABELS = {
    "run": "Agent",
    "docs": "Docs",
    "tasks": "Tasks",
    "stories": "Stories",
    "plans": "Plans",
    "adr": "ADRs",
    "scenarios": "Scenarios",
    "questions": "Questions",
    "proposals": "Proposals",
    "metrics": "Metrics",
    "commits": "Commits",
    "code-refs": "Code refs",
    "search": "Search",
    "routines": "Routines",
    "costs": "Costs",
    "revisions": "Revisions",
}
_DIGITS = re.compile(r"(\d+)")


@dataclass(frozen=True)
class NavLink:
    href: str
    label: str


@dataclass(frozen=True)
class BackNav:
    back: NavLink
    # Пришли не из индекса вкладки: со списка с фильтрами или с другой сущности.
    has_context: bool = False
    prev: NavLink | None = None
    next: NavLink | None = None


def _label(slug: str, path: str, query: str, entity_label: EntityLabel | None = None) -> str:
    rest = path.removeprefix(f"/p/{slug}").strip("/")
    if not rest:
        return slug
    tab, _, tail = rest.partition("/")
    if tail:
        # Сущность: ключ — последний сегмент (`docs/a/b` → `a/b` целиком).
        key = tail if tab == "docs" else tail.rsplit("/", 1)[-1]
        named = entity_label(tab, key) if entity_label is not None else None
        return named or key
    label = _TAB_LABELS.get(tab, tab.replace("-", " ").capitalize())
    return f"{label} (filtered)" if query else label


def _source(request: Request, slug: str) -> str | None:
    """Адрес источника (`?from=` или `Referer`), если это другая страница проекта."""
    explicit = request.query_params.get(FROM_PARAM)
    raw = explicit if explicit else request.headers.get("referer")
    if not raw:
        return None
    if explicit and (urlsplit(raw).scheme or urlsplit(raw).netloc):
        return None  # `from` — только путь: хост в нём не нужен никому
    found = same_site_path(request, raw)
    if found is None:
        return None
    path = unquote(urlsplit(found).path)
    if path != f"/p/{slug}" and not path.startswith(f"/p/{slug}/"):
        return None
    # `/p/<slug>/../other` проходит префикс, но браузер свернёт его в чужой
    # проект: сегменты `.` и `..` не принимаем вовсе.
    if any(seg in {".", ".."} for seg in path.split("/")):
        return None
    # Сама себя: перезагрузка, правка поля с редиректом обратно.
    if path == request.url.path:  # Starlette уже декодировал путь запроса
        return None
    return found


def entity_labels(session: Session, project_id: int) -> EntityLabel:
    """Подписи родителей из БД: план в адресе — row_id, человеку нужен scope."""

    def label(tab: str, key: str) -> str | None:
        if tab != "plans" or not key.isdigit():
            return None
        plan = plan_service.get_for_project(session, int(key), project_id=project_id)
        return plan.scope if plan is not None else None

    return label


def natural_key(key: str) -> tuple[tuple[int, str], ...]:
    """`US-2` раньше `US-10`: числа сравниваются как числа."""
    return tuple(
        (int(part), "") if part.isdigit() else (0, part) for part in _DIGITS.split(key) if part
    )


def neighbors(
    keys: Sequence[str],
    current: str,
    href: Callable[[str], str],
    label: Callable[[str], str] | None = None,
) -> tuple[NavLink | None, NavLink | None]:
    """Соседи `current` в упорядоченном `keys`; нет его там — нет соседей.

    Подпись — сам ключ, если `label` не дан (план: ключ — row_id, подпись — scope).
    """
    try:
        i = list(keys).index(current)
    except ValueError:
        return None, None
    name = label or (lambda key: key)
    prev = NavLink(href(keys[i - 1]), name(keys[i - 1])) if i > 0 else None
    nxt = NavLink(href(keys[i + 1]), name(keys[i + 1])) if i + 1 < len(keys) else None
    return prev, nxt


def back_nav(
    request: Request,
    slug: str,
    *,
    tab: str,
    order: Sequence[str] = (),
    current: str | None = None,
    href: Callable[[str], str] | None = None,
    label: Callable[[str], str] | None = None,
    entity_label: EntityLabel | None = None,
) -> BackNav:
    """Полоса страницы сущности вкладки `tab`; соседи — по `order`, если дан.

    `entity_label(tab, key)` подписывает родителя-сущность, чей ключ в
    адресе не читается человеком (план: `plans/7` → его scope).
    """
    default = f"/p/{slug}/{tab}"
    source = _source(request, slug)
    if source is None:
        back = NavLink(default, _TAB_LABELS.get(tab, tab))
    else:
        parts = urlsplit(source)
        back = NavLink(source, _label(slug, unquote(parts.path), parts.query, entity_label))
    has_context = back.href != default
    prev = nxt = None
    if order and current is not None and href is not None:
        to_key = href

        def step(key: str) -> str:
            # Шаг к соседу несёт родителя с собой: иначе Referer соседа —
            # эта страница, и «назад» после трёх шагов вело бы по соседям.
            target = to_key(key)
            return f"{target}?{urlencode({FROM_PARAM: back.href})}" if has_context else target

        prev, nxt = neighbors(order, current, step, label)
    return BackNav(back=back, has_context=has_context, prev=prev, next=nxt)
