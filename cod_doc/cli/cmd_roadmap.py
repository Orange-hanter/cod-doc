"""`cod-doc roadmap show` — очередь работ проекта и прогноз (RFC 36 §3.6, ROAD-006).

Зеркало MCP-тула ``roadmap_get``: та же форма ``--json``
(``services.serializers.roadmap_to_dict``, контракт C-04). Даты — прогноз по
пропускной способности, не сроки (C-19). Порядок планов меняет
``cod-doc plan move``.

Импорты ``infra``/``services`` — в теле команды: ``import cod_doc.cli`` не
тянет SQLAlchemy (ADO-179). ``--json`` печатается через ``click.echo``, а не
rich (ADO-176).
"""

from __future__ import annotations

import json as _json
from datetime import date
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from cod_doc.config import Config

console = Console()

_FLAG_MARKS = {
    "in_progress": "▶",
    "blocked": "⛔",
    "pulled": "↑",
    "cross_plan": "⇄",
    "backlog": "·",
}
_WARNINGS = {
    "inflow_exceeds": "за окно создано больше задач, чем закрыто — даты оптимистичны",
    "short_sample": "истории меньше 4 недель — показан только порядок",
    "zero_sample": "за окно не закрыто ни одной задачи — показан только порядок",
    "beyond_horizon": "хвост очереди дальше 26 недель — без дат",
}


@click.group()
def roadmap() -> None:
    """Дорожная карта: порядок работ проекта и прогноз «когда очередь дойдёт»."""


@roadmap.command("show")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--plan", "plan_scope", default=None, help="Только строки этого плана")
@click.option("--include-backlog", is_flag=True, default=False, help="Включить backlog")
@click.option("--weeks", type=int, default=8, show_default=True, help="Окно выборки, недель")
@click.option("--seed", type=int, default=0, show_default=True, help="Seed Монте-Карло")
@click.option(
    "--asof",
    default=None,
    help="Дата отсчёта прогноза YYYY-MM-DD (по умолчанию — сегодня, UTC)",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def roadmap_show(
    ctx: click.Context,
    project: str,
    plan_scope: str | None,
    include_backlog: bool,
    weeks: int,
    seed: int,
    asof: str | None,
    as_json: bool,
) -> None:
    """Очередь задач всего проекта с прогнозом p50/p85.

    Порядок: план (cod-doc plan move) → секция → приоритет → task_id;
    блокер подтягивается к задаче, которую держит, даже из другого плана.
    Даты — когда очередь примерно дойдёт до задачи по закрытым за неделю,
    не сроки.
    """
    from datetime import UTC, datetime

    from cod_doc.cli._common import require_project_id_click
    from cod_doc.infra.db import db_for_entry, transactional
    from cod_doc.services import roadmap_service
    from cod_doc.services.serializers import roadmap_to_dict

    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(project)
    if entry is None:
        raise click.ClickException(f"Project not found: {project}")
    try:
        day = date.fromisoformat(asof) if asof else datetime.now(UTC).date()
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--asof") from None

    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            project_id = require_project_id_click(session, project)
            try:
                built = roadmap_service.build(
                    session,
                    project_id=project_id,
                    asof=day,
                    include_backlog=include_backlog,
                    weeks=weeks,
                    seed=seed,
                )
            except roadmap_service.RoadmapCycleError as exc:
                raise click.ClickException(str(exc)) from None
    finally:
        engine.dispose()
    payload = roadmap_to_dict(built, plan_scope=plan_scope)

    if as_json:
        click.echo(_json.dumps(payload, ensure_ascii=False))
        return
    _print(payload)


def _print(payload: dict[str, Any]) -> None:
    forecast = payload["forecast"]
    console.print(
        f"Дорожная карта на {forecast['asof']} — даты ≈ прогноз по пропускной "
        f"способности за {len(forecast['sample'])} нед., не сроки",
        style="bold",
        markup=False,
    )
    plans = Table(show_header=True, header_style="bold")
    for col in ("План", "Позиции", "Открыто", "p50 старт", "p50 финиш", "p85 финиш"):
        plans.add_column(col)
    dates = {p["scope"]: p for p in forecast["plans"]}
    for bar in payload["queue"]["plans"]:
        when = dates.get(bar["scope"], {})
        # Данные проекта — в Text, а не строкой: ячейка Table читает строку
        # как rich-разметку, и заголовок с «[…]» ломал бы вывод.
        plans.add_row(
            Text(bar["scope"]),
            f"{bar['first_position']}–{bar['last_position']}",
            str(bar["open_count"]),
            when.get("p50_start") or "—",
            when.get("p50_finish") or "—",
            when.get("p85_finish") or "—",
        )
    console.print(plans)

    rows = Table(show_header=True, header_style="bold")
    for col in ("#", "Задача", "План", "Секция", "Статус", "p50", "p85", ""):
        rows.add_column(col)
    for row in payload["queue"]["rows"]:
        marks = "".join(_FLAG_MARKS[f] for f in row["flags"])
        if row["pulled_by"]:
            marks += f" ради {', '.join(row['pulled_by'])}"
        rows.add_row(
            str(row["position"]),
            Text(f"{row['task_id']} {row['title']}"),
            Text(row["plan_scope"]),
            row["section_letter"],
            row["status"],
            row["p50"] or "—",
            row["p85"] or "—",
            Text(marks),
        )
    console.print(rows)
    for warning in forecast["warnings"]:
        console.print(f"⚠ {_WARNINGS.get(warning, warning)}", style="yellow", markup=False)
