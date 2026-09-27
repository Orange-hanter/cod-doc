"""`plan list` — все планы проекта со статусом и счётчиками (RFC 27 F8, AFT-007).

Зеркало MCP-тула ``plan_list`` поверх ``plan_service.list_plans_summary``.

``--json`` печатается через ``click.echo``, а не rich: перенос строки и
разметка молча портят значения (ADO-176).
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _STATUS_ICON, _guard, _make_session, _project_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config

#: Столько символов principle помещается в колонку таблицы.
_PRINCIPLE_WIDTH = 60


@plan.command("list")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option(
    "--status",
    default=None,
    help="Фильтр по статусу плана: empty, pending, in-progress (или in_progress), done",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def plan_list(ctx: click.Context, project: str, status: str | None, as_json: bool) -> None:
    """Все планы проекта: scope, статус, done/total, остаток и principle.

    Пример: cod-doc plan list -p cod-doc --status in-progress --json
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        pid = _project_id(session, project)

    with _guard(), transactional(sf) as session:
        rows = plan_service.list_plans_summary(session, pid, status=status)

    if as_json:
        click.echo(_json.dumps(rows, ensure_ascii=False))
        return

    if not rows:
        console.print(f"В проекте {project} нет планов.", style="dim", markup=False)
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Scope", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Done/Total", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("Principle")
    for row in rows:
        principle = row["principle"] or ""
        if len(principle) > _PRINCIPLE_WIDTH:
            principle = principle[: _PRINCIPLE_WIDTH - 1] + "…"
        icon = _STATUS_ICON.get(row["status"], "⚪")
        table.add_row(
            row["scope"],
            f"{icon} {row['status']}",
            f"{row['done']}/{row['total']}",
            str(row["remaining"]),
            principle,
        )
    console.print(table)
