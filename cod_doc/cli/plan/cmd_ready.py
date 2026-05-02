"""`plan ready` — list tasks ready to work on."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _make_session, _require_plan_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("ready")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--limit", default=10, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def plan_ready(
    ctx: click.Context, plan_scope: str, project: str, limit: int, as_json: bool
) -> None:
    """List tasks ready to work on (all blocking deps done)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        plan_id = _require_plan_id(session, plan_scope)
        tasks = plan_service.ready(session, plan_id, limit=limit)

    if as_json:
        console.print(
            _json.dumps(
                [
                    {
                        "task_id": t.task_id,
                        "title": t.title,
                        "priority": t.priority.value,
                        "type": t.type.value,
                    }
                    for t in tasks
                ],
                indent=2,
            )
        )
        return

    if not tasks:
        console.print("[dim]No ready tasks.[/dim]")
        return

    table = Table(title=f"Ready tasks — {plan_scope}", show_header=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Priority", width=10)
    table.add_column("Type", width=10)
    table.add_column("Title")
    for t in tasks:
        table.add_row(t.task_id, t.priority.value, t.type.value, t.title)
    console.print(table)
