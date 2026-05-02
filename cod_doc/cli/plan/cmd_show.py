"""`plan show` — progress overview for a plan."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _STATUS_ICON, _make_session, _require_plan_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("show")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def plan_show(ctx: click.Context, plan_scope: str, project: str, as_json: bool) -> None:
    """Show progress overview for a plan."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        plan_id = _require_plan_id(session, plan_scope)
        progress = plan_service.recalc(session, plan_id)

    if as_json:
        console.print(
            _json.dumps(
                {
                    "plan_id": progress.plan_id,
                    "scope": progress.scope,
                    "total": progress.total,
                    "done": progress.done,
                    "in_progress": progress.in_progress,
                    "remaining": progress.remaining,
                    "status": progress.status.value,
                    "sections": [
                        {
                            "letter": s.letter,
                            "title": s.title,
                            "total": s.total,
                            "done": s.done,
                            "remaining": s.remaining,
                            "status": s.status.value,
                        }
                        for s in progress.sections
                    ],
                },
                indent=2,
            )
        )
        return

    console.rule(f"[bold cyan]Plan: {progress.scope}[/bold cyan]")
    console.print(
        f"  Total: [bold]{progress.total}[/bold]  "
        f"Done: [green]{progress.done}[/green]  "
        f"Remaining: [yellow]{progress.remaining}[/yellow]  "
        f"Status: {_STATUS_ICON.get(progress.status.value, '⚪')} {progress.status.value}"
    )
    console.print()

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Section", style="cyan")
    table.add_column("Total", justify="right")
    table.add_column("Done", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("Status")
    for s in progress.sections:
        icon = _STATUS_ICON.get(s.status.value, "⚪")
        table.add_row(
            f"{s.letter}: {s.title}",
            str(s.total),
            str(s.done),
            str(s.remaining),
            f"{icon} {s.status.value}",
        )
    console.print(table)
