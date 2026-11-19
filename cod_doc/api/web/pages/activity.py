"""Лента активности проекта — `/p/{slug}/activity` (WUI-012, кит Ledger).

Журнал записей (`activity_event`) за последние дни, по дням. Главная беда
живых проектов — шум: у Orakul за неделю 22 тыс. событий, из них 95% — рутины
(`link.verified`, `routine.fired`). Поэтому по умолчанию лента показывает
людей и агентов, а события рутин и системы сворачивает в одну сводку дня по
видам («link.verified ×2940»). Вид «Рутины» и «Всё» — по запросу.

Агрегаты — одним `GROUP BY` (`activity_service.summarize`), строки ленты — по
одному запросу на вид актора с лимитом, без выгрузки всей недели.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.navigation import hit_url
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import ActorKind
from cod_doc.services import activity_service

router = APIRouter()

#: Шум: события, которые пишет автоматика, а не человек или агент за работой.
NOISE_KINDS: Final[frozenset[str]] = frozenset({ActorKind.ROUTINE.value, ActorKind.SYSTEM.value})
#: Виды актора, чьи события лента показывает по умолчанию.
PEOPLE_KINDS: Final[tuple[str, ...]] = tuple(
    k.value for k in ActorKind if k.value not in NOISE_KINDS
)
#: Окно ленты в днях.
WINDOW_DAYS: Final[int] = 7
#: Сколько последних событий на вид актора читать для ленты.
ROWS_PER_KIND: Final[int] = 150
#: Сколько видов событий показывать в боковой сводке.
TOP_KINDS: Final[int] = 10
_WHO: Final[tuple[str, ...]] = ("people", "routines", "all")
_SCOPE_PATH: Final[dict[str, str]] = {"document": "doc", "doc": "doc", "task": "task"}


def _rows(
    session: Session, project_id: int, kinds: tuple[str, ...], since: datetime
) -> list[dict[str, Any]]:
    """Последние события перечисленных видов актора, свежие сверху."""
    items: list[dict[str, Any]] = []
    for kind in kinds:
        page = activity_service.list_events(
            session, project_id, actor_kind=kind, since=since, limit=ROWS_PER_KIND
        )
        items.extend(page["items"])
    items.sort(key=lambda e: e["ts"] or "", reverse=True)
    return items


def _with_links(slug: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for event in events:
        path_kind = _SCOPE_PATH.get(event.get("scope_kind") or "", event.get("scope_kind") or "")
        ref = event.get("scope_id")
        event["href"] = hit_url(slug, path_kind, ref) if ref else None
    return events


def _by_day(
    events: list[dict[str, Any]], summary: list[dict[str, Any]], show_noise_rows: bool
) -> list[dict[str, Any]]:
    """Дни свежие сверху: строки событий и сводка шума дня по видам."""
    noise: dict[str, dict[str, int]] = {}
    for item in summary:
        if item["actor_kind"] in NOISE_KINDS:
            day_noise = noise.setdefault(item["day"], {})
            day_noise[item["kind"]] = day_noise.get(item["kind"], 0) + item["n"]
    rows_by_day: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        rows_by_day.setdefault((event["ts"] or "")[:10], []).append(event)
    days = sorted(set(rows_by_day) | set(noise), reverse=True)
    result = []
    for day in days:
        digest = sorted(noise.get(day, {}).items(), key=lambda kv: (-kv[1], kv[0]))
        result.append(
            {
                "day": day,
                "rows": rows_by_day.get(day, []),
                "digest": [] if show_noise_rows else digest,
                "noise_total": sum(n for _, n in digest),
            }
        )
    return result


@router.get("/p/{slug}/activity", response_class=HTMLResponse)
def activity_feed(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    who: str = "people",
) -> HTMLResponse:
    """Лента за неделю: люди и агенты строками, рутины — сводкой дня."""
    proj = get_project(slug)
    session, project_id = db
    if who not in _WHO:
        who = "people"
    since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)

    summary = activity_service.summarize(
        session, project_id, since=since, group_by=("day", "actor_kind", "kind")
    )
    total = sum(item["n"] for item in summary)
    noise_total = sum(item["n"] for item in summary if item["actor_kind"] in NOISE_KINDS)
    by_kind: dict[str, int] = {}
    for item in summary:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + item["n"]
    top = sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_KINDS]

    kinds = {
        "people": PEOPLE_KINDS,
        "routines": tuple(sorted(NOISE_KINDS)),
        "all": tuple(k.value for k in ActorKind),
    }[who]
    events = _with_links(slug, _rows(session, project_id, kinds, since))

    return templates.TemplateResponse(
        request,
        "project/activity.html",
        {
            "project": proj.entry,
            "who": who,
            "days": _by_day(events, summary, show_noise_rows=who != "people"),
            "counts": {"people": total - noise_total, "routines": noise_total, "all": total},
            "top_kinds": top,
            "top_max": top[0][1] if top else 0,
            "window_days": WINDOW_DAYS,
            "rows_per_kind": ROWS_PER_KIND,
            "noise_kinds": NOISE_KINDS,
        },
    )
