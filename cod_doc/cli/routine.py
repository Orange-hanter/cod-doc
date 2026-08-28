"""CLI commands for routines: routine list/tick/run (ADO-024)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config, ProjectEntry

console = Console()
log = get_logger("cli.routine")


def _open_entry(
    cfg: Config, project_name: str
) -> tuple[ProjectEntry, sessionmaker[Session], Engine]:
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project_name)
    if entry is None:
        raise click.ClickException(f"Project not found: {project_name}")
    factory, engine = db_for_entry(entry)
    return entry, factory, engine


def _require_project_id(session: Session, project_name: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        raise click.ClickException(
            f"Project '{project_name}' is not registered in the DB. "
            "Run `cod-doc project init {name}` first."
        )
    return proj.row_id


@click.group()
def routine() -> None:
    """Inspect and run routines (scheduled health checks)."""


# ──────────────────────────────────────────────────────────────────────────────
# routine list
# ──────────────────────────────────────────────────────────────────────────────


@routine.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.pass_context
def routine_list(ctx: click.Context, project: str) -> None:
    """List routines with last-run status and time."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import routine_service

    cfg: Config = ctx.obj["config"]
    _entry, factory, engine = _open_entry(cfg, project)
    try:
        with transactional(factory) as session:
            project_id = _require_project_id(session, project)
            rows = [
                (r, routine_service.history(session, project_id, r.name, limit=1))
                for r in routine_service.list_routines(session, project_id)
            ]
    finally:
        engine.dispose()

    if not rows:
        console.print("[dim]No routines found.[/dim]")
        return

    table = Table(title=f"Routines — {project}", show_header=True)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Check")
    table.add_column("Trigger")
    table.add_column("Cron")
    table.add_column("Enabled")
    table.add_column("Last status")
    table.add_column("Last run at")
    for r, runs in rows:
        last = runs[0] if runs else None
        table.add_row(
            r.name,
            r.check_name,
            r.trigger,
            r.cron or "—",
            "yes" if r.enabled else "no",
            last.status if last else "—",
            last.started_at.isoformat()[:19] if last else "—",
        )
    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# routine tick
# ──────────────────────────────────────────────────────────────────────────────


@routine.command("tick")
@click.option("--project", "-p", required=True, help="Project slug")
@click.pass_context
def routine_tick(ctx: click.Context, project: str) -> None:
    """Fire all due cron routines (one scheduler tick, for OS cron/launchd)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import routine_service

    cfg: Config = ctx.obj["config"]
    _entry, factory, engine = _open_entry(cfg, project)
    try:
        with transactional(factory) as session:
            project_id = _require_project_id(session, project)
            fired = routine_service.tick(session, project_id)
    finally:
        engine.dispose()

    if fired:
        console.print(f"[green]Fired {len(fired)} routine(s):[/green] {', '.join(fired)}")
    else:
        console.print("[dim]Nothing due.[/dim]")


# ──────────────────────────────────────────────────────────────────────────────
# routine run
# ──────────────────────────────────────────────────────────────────────────────


@routine.command("run")
@click.argument("name")
@click.option("--project", "-p", required=True, help="Project slug")
@click.pass_context
def routine_run(ctx: click.Context, name: str, project: str) -> None:
    """Manually run one routine by name."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import routine_service
    from cod_doc.services.routine_service import RoutineNotFoundError

    cfg: Config = ctx.obj["config"]
    _entry, factory, engine = _open_entry(cfg, project)
    try:
        with transactional(factory) as session:
            project_id = _require_project_id(session, project)
            try:
                run = routine_service.run_now(session, project_id, name)
            except RoutineNotFoundError:
                raise click.ClickException(f"Routine '{name}' not found.") from None
    finally:
        engine.dispose()

    console.print(f"[green]{name}[/green]: status={run.status} findings={run.findings_count}")
