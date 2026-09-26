"""CLI-команды журнала событий: activity summary."""

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
log = get_logger("cli.activity")

_GROUP_BY_CHOICES = ["day", "actor_kind", "kind", "scope_kind"]


def _make_session(project_name: str, cfg: Config) -> sessionmaker[Session]:
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    factory, _engine = db_for_entry(entry)
    return factory


def _require_project_id(session: Session, project_name: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        console.print(f"[red]Project '{project_name}' not in DB. Run 'project add' first.[/red]")
        sys.exit(1)
    return proj.row_id


@click.group()
def activity() -> None:
    """Журнал activity_event проекта."""


@activity.command("summary")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--since",
    required=True,
    help="ISO-8601: события не раньше момента, напр. 2026-09-12",
)
@click.option(
    "--until",
    default=None,
    help="ISO-8601: события не позже момента (по умолчанию — без верхней границы)",
)
@click.option(
    "--group-by",
    "group_by",
    multiple=True,
    type=click.Choice(_GROUP_BY_CHOICES),
    default=("day",),
    show_default=True,
    help="Ключи агрегации; повторяйте опцию для нескольких",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def activity_summary(
    ctx: click.Context,
    project: str,
    since: str,
    until: str | None,
    group_by: tuple[str, ...],
    as_json: bool,
) -> None:
    """Агрегаты журнала событий (GROUP BY) за период.

    \b
    События за 14 дней по дням и actor_kind:
      cod-doc activity summary -p X --since 2026-09-12 \\
          --group-by day --group-by actor_kind --json
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import activity_service, task_service

    try:
        since_dt = task_service.parse_since(since)
        until_dt = task_service.parse_since(until) if until else None
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from None

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        try:
            rows = activity_service.summarize(
                session, project_id, since=since_dt, until=until_dt, group_by=group_by
            )
        except ValueError as exc:
            raise click.UsageError(str(exc)) from None

    if as_json:
        click.echo(_json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if not rows:
        console.print("[dim]No events found.[/dim]")
        return

    table = Table(title=f"Activity summary — project {project}", show_header=True)
    for key in group_by:
        table.add_column(key)
    table.add_column("n", justify="right")
    total = 0
    for row in rows:
        total += int(row["n"])
        table.add_row(*[str(row.get(key) or "—") for key in group_by], str(row["n"]))
    table.add_row("итого", *[""] * (len(group_by) - 1), str(total), style="bold")
    console.print(table)
