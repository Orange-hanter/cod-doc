"""CLI commands for task management: task list/show/create/status/complete."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config

console = Console()
log = get_logger("cli.task")

_STATUS_ICON = {
    "pending": "🟡",
    "in-progress": "🔵",
    "done": "🟢",
}


def _make_session(project_name: str, cfg: Config) -> sessionmaker[Session]:
    from pathlib import Path

    from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    url = resolve_db_url(Path(entry.path))
    engine = make_engine(url)
    return make_session_factory(engine)


def _require_project_id(session: Session, project_name: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        console.print(f"[red]Project '{project_name}' not in DB. Run 'project add' first.[/red]")
        sys.exit(1)
    return proj.row_id


@click.group()
def task() -> None:
    """Manage tasks (create, status, complete, list)."""


# ──────────────────────────────────────────────────────────────────────────────
# task list
# ──────────────────────────────────────────────────────────────────────────────


@task.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--status",
    "-s",
    "filter_status",
    default=None,
    type=click.Choice(["pending", "in-progress", "done"]),
    help="Filter by status",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def task_list(ctx: click.Context, project: str, filter_status: str | None, as_json: bool) -> None:
    """List tasks for a project."""
    from cod_doc.domain.entities import TaskStatus
    from cod_doc.infra.db import transactional
    from cod_doc.services import task_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    status_enum = TaskStatus(filter_status) if filter_status else None

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        tasks = task_service.list_for_project(session, project_id, status=status_enum)

    if as_json:
        data = [
            {
                "task_id": t.task_id,
                "title": t.title,
                "status": t.status.value,
                "type": t.type.value,
                "priority": t.priority.value,
            }
            for t in tasks
        ]
        console.print(_json.dumps(data, ensure_ascii=False, indent=2))
        return

    if not tasks:
        console.print("[dim]No tasks found.[/dim]")
        return

    table = Table(title=f"Tasks — {project}", show_header=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", width=14)
    table.add_column("Priority", width=10)
    table.add_column("Type", width=10)
    table.add_column("Title")
    for t in tasks:
        icon = _STATUS_ICON.get(t.status.value, "⚪")
        table.add_row(
            t.task_id,
            f"{icon} {t.status.value}",
            t.priority.value,
            t.type.value,
            t.title,
        )
    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# task show
# ──────────────────────────────────────────────────────────────────────────────


@task.command("show")
@click.argument("task_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def task_show(ctx: click.Context, task_id: str, project: str, as_json: bool) -> None:
    """Show details of a single task."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import task_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        t = task_service.get(session, task_id)

    if t is None:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        sys.exit(1)

    if as_json:
        console.print(
            _json.dumps(
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "status": t.status.value,
                    "type": t.type.value,
                    "priority": t.priority.value,
                    "description": t.description,
                    "acceptance": t.acceptance,
                    "created": t.created.isoformat() if t.created else None,
                    "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                    "completed_commit": t.completed_commit,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    icon = _STATUS_ICON.get(t.status.value, "⚪")
    console.rule(f"[bold cyan]{t.task_id}[/bold cyan]")
    console.print(f"  Title:    {t.title}")
    console.print(f"  Status:   {icon} {t.status.value}")
    console.print(f"  Type:     {t.type.value}")
    console.print(f"  Priority: {t.priority.value}")
    if t.description:
        console.print(f"\n[bold]Description:[/bold]\n{t.description}")
    if t.acceptance:
        console.print(f"\n[bold]Acceptance:[/bold]\n{t.acceptance}")
    if t.completed_at:
        console.print(f"\n  Completed: {t.completed_at.isoformat()[:19]}")
    if t.completed_commit:
        console.print(f"  Commit:    {t.completed_commit}")


# ──────────────────────────────────────────────────────────────────────────────
# task create
# ──────────────────────────────────────────────────────────────────────────────


@task.command("create")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--plan", "plan_scope", required=True, help="Plan scope (e.g. 'cod-doc')")
@click.option("--section", "section_letter", required=True, help="Section letter (e.g. 'A')")
@click.option("--title", required=True, help="Task title")
@click.option(
    "--type",
    "task_type",
    required=True,
    type=click.Choice(["feature", "test", "bug", "refactor", "migration", "docs", "chore"]),
)
@click.option(
    "--priority",
    required=True,
    type=click.Choice(["critical", "high", "medium", "low"]),
)
@click.option("--id", "task_id", default=None, help="Explicit task ID (e.g. COD-042)")
@click.option("--prefix", default=None, help="ID prefix for auto-numbering (e.g. COD)")
@click.option("--description", default=None)
@click.option("--acceptance", default=None)
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def task_create(
    ctx: click.Context,
    project: str,
    plan_scope: str,
    section_letter: str,
    title: str,
    task_type: str,
    priority: str,
    task_id: str | None,
    prefix: str | None,
    description: str | None,
    acceptance: str | None,
    author: str,
    reason: str | None,
) -> None:
    """Create a new task in a plan section."""
    from cod_doc.domain.entities import Priority, TaskType
    from cod_doc.infra.db import transactional
    from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository
    from cod_doc.services import task_service
    from cod_doc.services.validation import ValidationError

    if task_id is None and prefix is None:
        console.print("[red]Provide --id or --prefix.[/red]")
        sys.exit(1)

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    # Phase 1: resolve IDs (read-only — sys.exit here is safe, nothing to commit)
    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        plan = PlanRepository(session).get_by_scope(plan_scope)
        if plan is None or plan.row_id is None:
            console.print(f"[red]Plan '{plan_scope}' not found.[/red]")
            sys.exit(1)
        sections = PlanSectionRepository(session).list_for_plan(plan.row_id)
        section = next((s for s in sections if s.letter.upper() == section_letter.upper()), None)
        if section is None or section.row_id is None:
            letters = ", ".join(s.letter for s in sections)
            console.print(f"[red]Section '{section_letter}' not found. Available: {letters}[/red]")
            sys.exit(1)
        plan_id = plan.row_id
        section_id = section.row_id

    # Phase 2: write (separate transaction — no sys.exit inside this block)
    try:
        with transactional(sf) as session:
            t = task_service.create(
                session,
                project_id=project_id,
                plan_id=plan_id,
                section_id=section_id,
                title=title,
                type=TaskType(task_type),
                priority=Priority(priority),
                author=author,
                task_id=task_id,
                id_prefix=prefix,
                description=description,
                acceptance=acceptance,
                reason=reason,
            )
    except ValidationError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        sys.exit(1)

    console.print(f"[green]✅ Created task [bold]{t.task_id}[/bold]: {t.title}[/green]")


# ──────────────────────────────────────────────────────────────────────────────
# task status
# ──────────────────────────────────────────────────────────────────────────────


@task.command("status")
@click.argument("task_id")
@click.argument(
    "new_status", metavar="STATUS", type=click.Choice(["pending", "in-progress", "done"])
)
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def task_status(
    ctx: click.Context,
    task_id: str,
    new_status: str,
    project: str,
    author: str,
    reason: str | None,
) -> None:
    """Update task status (does not validate deps; use 'complete' for done)."""
    from cod_doc.domain.entities import TaskStatus
    from cod_doc.infra.db import transactional
    from cod_doc.services import task_service
    from cod_doc.services.task_service import TaskNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            t = task_service.update_status(
                session,
                task_id=task_id,
                new_status=TaskStatus(new_status),
                author=author,
                reason=reason,
            )
    except TaskNotFoundError:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        sys.exit(1)

    icon = _STATUS_ICON.get(t.status.value, "⚪")
    console.print(f"[green]{t.task_id}: {icon} {t.status.value}[/green]")


# ──────────────────────────────────────────────────────────────────────────────
# task complete
# ──────────────────────────────────────────────────────────────────────────────


@task.command("complete")
@click.argument("task_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--commit", default=None, help="Git commit SHA")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def task_complete(
    ctx: click.Context,
    task_id: str,
    project: str,
    commit: str | None,
    author: str,
    reason: str | None,
) -> None:
    """Complete a task (validates blocking deps are done first)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services.task_service import (
        TaskAlreadyDoneError,
        TaskBlockedError,
        TaskNotFoundError,
        complete,
    )

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            t = complete(
                session,
                task_id=task_id,
                author=author,
                commit_sha=commit,
                reason=reason,
            )
    except TaskNotFoundError:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        sys.exit(1)
    except TaskAlreadyDoneError:
        console.print(f"[yellow]{task_id} is already done.[/yellow]")
        return
    except TaskBlockedError as exc:
        console.print(f"[red]Blocked: {exc}[/red]")
        sys.exit(1)

    console.print(f"[green]✅ {t.task_id} marked done.[/green]")
    if t.completed_commit:
        console.print(f"   Commit: {t.completed_commit}")
