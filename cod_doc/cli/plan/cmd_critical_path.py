"""`plan critical-path` — longest sequential chain in a plan."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click

from ._common import _STATUS_ICON, _make_session, _require_plan_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("critical-path")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def plan_critical_path(ctx: click.Context, plan_scope: str, project: str, as_json: bool) -> None:
    """Show the critical path (longest sequential chain) for a plan."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        plan_id = _require_plan_id(session, plan_scope)
        result = plan_service.critical_path(session, plan_id)

    if as_json:
        console.print(
            _json.dumps(
                {
                    "plan_id": result.plan_id,
                    "length": result.length,
                    "task_ids": result.task_ids,
                    "chain": [
                        {
                            "task_id": e.task_id,
                            "title": e.title,
                            "status": e.status.value,
                            "depth": e.depth,
                        }
                        for e in result.chain
                    ],
                },
                indent=2,
            )
        )
        return

    console.rule(f"[bold]Critical path — {plan_scope} (length {result.length})[/bold]")
    if result.length == 0:
        console.print("[dim]Empty plan.[/dim]")
        return

    for entry in result.chain:
        icon = _STATUS_ICON.get(entry.status.value, "⚪")
        prefix = "  " * entry.depth + "→ " if entry.depth else "  "
        console.print(f"{prefix}[cyan]{entry.task_id}[/cyan] {icon} {entry.title}")
