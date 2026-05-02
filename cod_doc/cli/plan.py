"""CLI commands for plan management: plan show/ready/audit/export/critical-path/forward/reverse."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from cod_doc.config import Config
    from cod_doc.services.plan_service import ChainEntry

console = Console()
log = get_logger("cli.plan")

_STATUS_ICON = {
    "pending": "🟡",
    "in-progress": "🔵",
    "done": "🟢",
    "empty": "⬜",
}


def _make_session(project_name: str, cfg: Config):  # type: ignore[no-untyped-def]
    from pathlib import Path

    from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    url = resolve_db_url(Path(entry.path))
    engine = make_engine(url)
    return make_session_factory(engine)


def _require_plan_id(session, plan_scope: str) -> int:  # type: ignore[no-untyped-def]
    from cod_doc.infra.repositories import PlanRepository

    plan = PlanRepository(session).get_by_scope(plan_scope)
    if plan is None or plan.row_id is None:
        console.print(f"[red]Plan '{plan_scope}' not found.[/red]")
        sys.exit(1)
    return plan.row_id


@click.group()
def plan() -> None:
    """Inspect and query plans (progress, ready tasks, audit, export)."""


# ──────────────────────────────────────────────────────────────────────────────
# plan show
# ──────────────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────────────
# plan ready
# ──────────────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────────────
# plan audit
# ──────────────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────────────
# plan export
# ──────────────────────────────────────────────────────────────────────────────


@plan.command("export")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--section",
    type=click.Choice(["progress_overview", "next_batch", "dependency_graph", "all"]),
    default="all",
    show_default=True,
)
@click.pass_context
def plan_export(ctx: click.Context, plan_scope: str, project: str, section: str) -> None:
    """Export markdown projections for a plan."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        plan_id = _require_plan_id(session, plan_scope)
        projections = plan_service.export(session, plan_id)

    keys = list(projections.keys()) if section == "all" else [section]
    for key in keys:
        console.print(projections[key])


# ──────────────────────────────────────────────────────────────────────────────
# plan critical-path
# ──────────────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────────────
# plan forward / reverse (graph chains)
# ──────────────────────────────────────────────────────────────────────────────


def _render_chain(
    chain: list[ChainEntry],
    label: str,
    task_id: str,
    as_json: bool,
) -> None:
    if as_json:
        console.print(
            _json.dumps(
                [
                    {
                        "task_id": e.task_id,
                        "title": e.title,
                        "status": e.status.value,
                        "depth": e.depth,
                    }
                    for e in chain
                ],
                indent=2,
            )
        )
        return

    console.rule(f"[bold]{label} chain — {task_id}[/bold]")
    if not chain:
        console.print("[dim]No dependencies.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Depth", justify="right", width=6)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", width=14)
    table.add_column("Title")
    for e in chain:
        icon = _STATUS_ICON.get(e.status.value, "⚪")
        table.add_row(str(e.depth), e.task_id, f"{icon} {e.status.value}", e.title)
    console.print(table)


@plan.command("forward")
@click.argument("task_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def plan_forward(ctx: click.Context, task_id: str, project: str, as_json: bool) -> None:
    """Forward chain: prerequisites of TASK_ID (must complete BEFORE it)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service
    from cod_doc.services.plan_service import TaskNotFoundInPlanError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            chain = plan_service.forward_chain(session, task_id)
    except TaskNotFoundInPlanError:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        sys.exit(1)

    _render_chain(chain, "Forward", task_id, as_json)


@plan.command("reverse")
@click.argument("task_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def plan_reverse(ctx: click.Context, task_id: str, project: str, as_json: bool) -> None:
    """Reverse chain: tasks unblocked when TASK_ID completes."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service
    from cod_doc.services.plan_service import TaskNotFoundInPlanError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            chain = plan_service.reverse_chain(session, task_id)
    except TaskNotFoundInPlanError:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        sys.exit(1)

    _render_chain(chain, "Reverse", task_id, as_json)
