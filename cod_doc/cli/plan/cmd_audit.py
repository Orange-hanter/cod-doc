"""`plan audit` — integrity audit (cycles + done-drift)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_plan_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("audit")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def plan_audit(ctx: click.Context, plan_scope: str, project: str, as_json: bool) -> None:
    """Run integrity audit: cycle detection + done-drift check."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        plan_id = _require_plan_id(session, plan_scope)
        report = plan_service.audit(session, plan_id)

    if as_json:
        console.print(
            _json.dumps(
                {
                    "plan_id": report.plan_id,
                    "issues_total": report.issues_total,
                    "cycles": report.cycles,
                    "done_with_unfinished_blocks": report.done_with_unfinished_blocks,
                    "critical_path_length": report.critical_path_length,
                },
                indent=2,
            )
        )
        return

    console.rule(f"[bold]Audit — {plan_scope}[/bold]")
    console.print(f"  Critical path length: {report.critical_path_length}")

    if report.issues_total == 0:
        console.print("[green]✅ No issues found.[/green]")
        return

    if report.cycles:
        console.print(f"\n[bold red]Cycles ({len(report.cycles)}):[/bold red]")
        for cyc in report.cycles:
            console.print(f"  {' → '.join(cyc)}")

    if report.done_with_unfinished_blocks:
        console.print(
            f"\n[bold yellow]Done tasks with unfinished blocking deps "
            f"({len(report.done_with_unfinished_blocks)}):[/bold yellow]"
        )
        for tid in report.done_with_unfinished_blocks:
            console.print(f"  {tid}")

    sys.exit(1)
