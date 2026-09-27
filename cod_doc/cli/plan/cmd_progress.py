"""`plan progress` — прогресс одного плана или всех планов проекта (RFC 27 F8, AFT-007).

Зеркало MCP-тула ``plan_progress``: ``PLAN_SCOPE`` — один план с секциями,
``--all`` — все планы проекта, ``--all --by-section`` — все планы по секциям
одним вызовом. JSON той же формы, что у тула.

``--json`` печатается через ``click.echo``, а не rich: перенос строки и
разметка молча портят значения (ADO-176).
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING, Any

import click
from rich.table import Table

from ._common import _STATUS_ICON, _guard, _make_session, _project_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config
    from cod_doc.services.plan_service import PlanProgress


def _progress_to_dict(progress: PlanProgress, *, with_sections: bool) -> dict[str, Any]:
    """Строка плана — та же форма, что у MCP ``plan_progress``."""
    out: dict[str, Any] = {
        "scope": progress.scope,
        "total": progress.total,
        "done": progress.done,
        "in_progress": progress.in_progress,
        "cancelled": progress.cancelled,
        "remaining": progress.remaining,
        "status": progress.status.value,
    }
    if with_sections:
        out["sections"] = [
            {
                "letter": s.letter,
                "title": s.title,
                "total": s.total,
                "done": s.done,
                "cancelled": s.cancelled,
                "remaining": s.remaining,
                "status": s.status.value,
            }
            for s in progress.sections
        ]
    return out


def _sections_table(row: dict[str, Any]) -> Table:
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Section", style="cyan")
    table.add_column("Total", justify="right")
    table.add_column("Done", justify="right")
    table.add_column("Cancelled", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("Status")
    for s in row["sections"]:
        table.add_row(
            f"{s['letter']}: {s['title']}",
            str(s["total"]),
            str(s["done"]),
            str(s["cancelled"]),
            str(s["remaining"]),
            f"{_STATUS_ICON.get(s['status'], '⚪')} {s['status']}",
        )
    return table


def _render(rows: list[dict[str, Any]], *, with_sections: bool) -> None:
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Scope", style="cyan", no_wrap=True)
    table.add_column("Total", justify="right")
    table.add_column("Done", justify="right")
    table.add_column("Cancelled", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("Status")
    for row in rows:
        table.add_row(
            row["scope"],
            str(row["total"]),
            str(row["done"]),
            str(row["cancelled"]),
            str(row["remaining"]),
            f"{_STATUS_ICON.get(row['status'], '⚪')} {row['status']}",
        )
    console.print(table)
    if not with_sections:
        return
    for row in rows:
        console.print()
        console.rule(f"[bold cyan]Plan: {row['scope']}[/bold cyan]")
        console.print(_sections_table(row))


@plan.command("progress")
@click.argument("plan_scope", required=False)
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--all", "all_plans", is_flag=True, default=False, help="Все планы проекта")
@click.option(
    "--by-section",
    is_flag=True,
    default=False,
    help="Вместе с --all: разбивка каждого плана по секциям",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def plan_progress(
    ctx: click.Context,
    plan_scope: str | None,
    project: str,
    all_plans: bool,
    by_section: bool,
    as_json: bool,
) -> None:
    """Прогресс плана PLAN_SCOPE по секциям или всех планов проекта (--all).

    Один план всегда показывается с секциями. --all даёт итоги всех планов,
    --all --by-section — ещё и секции каждого, одним запросом.

    Пример: cod-doc plan progress -p X --all --by-section --json
    """
    if plan_scope is not None and all_plans:
        raise click.UsageError("PLAN_SCOPE и --all взаимоисключающие: укажи что-то одно.")
    if plan_scope is None and not all_plans:
        raise click.UsageError("Укажи PLAN_SCOPE или --all для всех планов проекта.")
    if by_section and not all_plans:
        raise click.UsageError(
            "--by-section работает только с --all: один план и так показан по секциям."
        )

    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        pid = _project_id(session, project)

    if plan_scope is None:
        with transactional(sf) as session:
            plans = plan_service.progress_for_project(session, pid, by_section=by_section)
        rows = [_progress_to_dict(p, with_sections=by_section) for p in plans]
        if as_json:
            click.echo(_json.dumps({"project": project, "plans": rows}, ensure_ascii=False))
            return
        if not rows:
            console.print(f"В проекте {project} нет планов.", style="dim", markup=False)
            return
        _render(rows, with_sections=by_section)
        return

    with _guard(), transactional(sf) as session:
        plan_id = plan_service.require_plan_in_project(session, pid, plan_scope)
        progress = plan_service.recalc(session, plan_id)
    row = _progress_to_dict(progress, with_sections=True)
    if as_json:
        click.echo(_json.dumps(row, ensure_ascii=False))
        return
    _render([row], with_sections=True)
