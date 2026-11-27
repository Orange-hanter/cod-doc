"""Панель «Связи» (RFC 39 §3.7, REL-012): контекст для `_frag/relations.html`.

Читает единую ленту `relation_service.relations`, группирует по `label` и
подбирает ссылку на страницу другого конца. Запись идёт через
`POST /p/{slug}/relations` (`pages/relations.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from cod_doc.services import relation_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_ROUTES = {
    "task": "tasks",
    "story": "stories",
    "adr": "adr",
    "scenario": "scenarios",
    "question": "questions",
    "document": "docs",
    "section": "docs",
}


def href(slug: str, ref: str) -> str:
    """Страница другого конца `kind:key`; пусто — у вида нет страницы (code, finding, …)."""
    kind, _, key = ref.partition(":")
    if kind in _ROUTES:
        return f"/p/{slug}/{_ROUTES[kind]}/{key}"
    if kind == "criterion":
        story, _, position = key.partition("#")
        return f"/p/{slug}/stories/{story}" + (f"#crit-{position}" if position else "")
    # `relate` пишет только http(s), но строка может прийти синком или из старых
    # таблиц: в href идёт лишь http(s), иначе `javascript:` стал бы XSS.
    if kind == "url" and urlsplit(key).scheme.lower() in {"http", "https"}:
        return key
    return ""


def relations_panel(session: Session, project_id: int, slug: str, ref: str) -> dict[str, Any]:
    """Контекст панели: группы по `label` и допустимые типы для формы «добавить»."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for v in relation_service.relations(session, project_id=project_id, ref=ref):
        groups.setdefault(v.label, []).append(
            {
                "ref": v.other_ref,
                "title": v.other_title or v.other_ref,
                "href": href(slug, v.other_ref),
                "external": v.other_ref.startswith("url:"),
                "source": v.source,
                "resolved": v.resolved,
                "note": v.note,
            }
        )
    # Бэкенд-типы пишутся своими тулами (relate их пока отвергает) — в форму не попадают.
    writable = [
        i.type.name
        for i in relation_service.relation_types(
            session, project_id=project_id, from_kind=ref.partition(":")[0]
        )
        if i.type.backend == "relation" and not i.deprecated
    ]
    return {
        "ref": ref,
        "back": href(slug, ref),
        "groups": [{"label": label, "rows": rows} for label, rows in groups.items()],
        "total": sum(len(r) for r in groups.values()),
        "types": writable,
    }
