"""`plan sections` — list sections with task counts."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _make_session, _require_plan_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("sections")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def plan_sections(ctx: click.Context, plan_scope: str, project: str, as_json: bool) -> None:
    """List plan sections with task counts (same payload as MCP ``plan_sections_list``)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        plan_id = _require_plan_id(session, plan_scope)
        rows = plan_service.sections_with_counts(session, plan_id)

    if as_json:
        console.print(_json.dumps(rows, indent=2))
        return

    if not rows:
        console.print("[dim]No sections.[/dim]")
        return

    table = Table(title=f"Sections — {plan_scope}", show_header=True, box=None, padding=(0, 2))
    table.add_column("Letter", style="cyan")
    table.add_column("Title")
    table.add_column("Tasks", justify="right")
    table.add_column("Done", justify="right")
    table.add_column("Pos", justify="right")
    for row in rows:
        table.add_row(
            row["letter"],
            row["title"],
            str(row["task_count"]),
            str(row["done_count"]),
            str(row["position"]),
        )
    console.print(table)
