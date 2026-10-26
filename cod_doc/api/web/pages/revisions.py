"""Project-wide revisions log."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.deps import get_project, try_open_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import EntityKind
from cod_doc.services import entity_ref_service
from cod_doc.services import revision_service as revisions
from cod_doc.services.entity_ref_service import EntityRef

router = APIRouter()

REVISIONS_PAGE_LIMIT = 50


def _as_kind(value: object) -> EntityKind | None:
    """Вид сущности ревизии; незнакомое (легаси) значение — None, а не 500."""
    try:
        return EntityKind(str(value))
    except ValueError:
        return None


def _entity_href(slug: str, ref: EntityRef) -> str:
    """Страница сущности по её публичному ключу (ADO-124)."""
    base = f"/p/{slug}"
    if ref.kind is EntityKind.TASK:
        return f"{base}/tasks/{ref.ref}"
    if ref.kind is EntityKind.DOCUMENT:
        return f"{base}/docs/{ref.ref}"
    if ref.kind is EntityKind.SECTION:
        return f"{base}/docs/{ref.ref}#{ref.anchor}"
    if ref.kind is EntityKind.STORY:
        return f"{base}/stories/{ref.ref}"
    if ref.kind is EntityKind.ADR:
        return f"{base}/adr/{ref.ref}"
    # План адресуется row_id — так устроен маршрут /plans/{plan_id}.
    return f"{base}/plans/{ref.row_id}"


def _entity_cell(
    slug: str,
    kind: str,
    row_id: int,
    refs: dict[tuple[EntityKind, int], EntityRef],
) -> dict[str, Any]:
    """Ячейка «сущность» журнала: публичный ключ и ссылка вместо `kind#row_id`.

    `entity_id` ревизии — внутренний row_id; ссылка на `/tasks/154` была бы
    404. Удалённая сущность остаётся текстом без ссылки, с пометкой; у видов
    без своей страницы (task_doc, link, …) — просто `kind#row_id`.
    """
    plain = f"{kind}#{row_id}"
    entity_kind = _as_kind(kind)
    if entity_kind is None:
        return {"kind": kind, "label": plain, "href": None, "missing": False, "title": ""}
    ref = refs.get((entity_kind, row_id))
    if ref is None:
        return {
            "kind": kind,
            "label": plain,
            "href": None,
            "missing": entity_kind in entity_ref_service.RESOLVERS,
            "title": "",
        }
    label = f"{ref.ref}#{ref.anchor}" if ref.anchor else ref.ref
    return {
        "kind": kind,
        "label": f"{kind} {label}",
        "href": _entity_href(slug, ref),
        "missing": False,
        "title": f"{ref.title} ({plain})",
    }


@router.get("/p/{slug}/revisions", response_class=HTMLResponse)
def revisions_log(
    request: Request,
    slug: str,
    entity_kind: str | None = None,
    entity_id: int | None = None,
) -> HTMLResponse:
    """Project-wide revisions log, optionally narrowed to one entity."""
    proj = get_project(slug)

    kind_filter: EntityKind | None = None
    kind_invalid = False
    if entity_kind:
        try:
            kind_filter = EntityKind(entity_kind)
        except ValueError:
            kind_invalid = True

    rows: list[dict[str, Any]] = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            found = revisions.list_for_project(
                session,
                project_db_id,
                limit=REVISIONS_PAGE_LIMIT,
                entity_kind=kind_filter,
                entity_id=entity_id if not kind_invalid else None,
            )
            # ADO-124: публичные ключи сущностей — пачкой на вид, не на строку.
            refs = entity_ref_service.resolve(
                session,
                project_db_id,
                (
                    (kind, r.entity_id)
                    for r in found
                    if (kind := _as_kind(r.entity_kind)) is not None
                ),
            )
            for r in found:
                rows.append(
                    {
                        "revision_id": r.revision_id,
                        "entity_kind": r.entity_kind.value
                        if isinstance(r.entity_kind, EntityKind)
                        else str(r.entity_kind),
                        "entity_id": r.entity_id,
                        "entity": _entity_cell(
                            proj.entry.name, str(r.entity_kind), r.entity_id, refs
                        ),
                        "author": r.author,
                        "at": r.at,
                        "reason": r.reason or "",
                        "diff_preview": revisions.diff_preview(r.diff),
                    }
                )

    return templates.TemplateResponse(
        request,
        "project/revisions.html",
        {
            "project": {"name": proj.entry.name},
            "revisions": rows,
            "db_available": db_available,
            "entity_kind": kind_filter.value if kind_filter else "",
            "entity_id": entity_id,
            "kind_invalid": kind_invalid,
            "kind_options": [k.value for k in EntityKind],
            "limit": REVISIONS_PAGE_LIMIT,
        },
    )
