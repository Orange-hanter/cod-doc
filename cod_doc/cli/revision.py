"""CLI commands for revision history: revision list/show/revert."""

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
log = get_logger("cli.revision")

_KIND_CHOICES = ["document", "section", "task", "plan", "story", "link", "module"]


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


def _resolve_entity_id(session: Session, kind: str, ref: str, project_id: int) -> int:
    """Resolve a user-supplied ref string to an entity row_id.

    Ref conventions by kind:
      task     → task_id string (e.g. COD-011)
      document → doc_key string (e.g. arch/data-model)
      story    → story_id string (e.g. US-001)
      section  → doc_key#anchor (e.g. arch/data-model#overview)
      plan/link/module/… → integer row_id as string
    """
    from sqlalchemy import select

    from cod_doc.infra.models import DocumentModel, SectionModel, TaskModel, UserStoryModel

    if kind == "task":
        row_id = session.execute(
            select(TaskModel.row_id).where(
                TaskModel.project_id == project_id,
                TaskModel.task_id == ref,
            )
        ).scalar_one_or_none()
        if row_id is None:
            console.print(f"[red]Task '{ref}' not found.[/red]")
            sys.exit(1)
        return int(row_id)

    if kind == "document":
        row_id = session.execute(
            select(DocumentModel.row_id).where(
                DocumentModel.project_id == project_id,
                DocumentModel.doc_key == ref,
            )
        ).scalar_one_or_none()
        if row_id is None:
            console.print(f"[red]Document '{ref}' not found.[/red]")
            sys.exit(1)
        return int(row_id)

    if kind == "story":
        row_id = session.execute(
            select(UserStoryModel.row_id).where(
                UserStoryModel.project_id == project_id,
                UserStoryModel.story_id == ref,
            )
        ).scalar_one_or_none()
        if row_id is None:
            console.print(f"[red]Story '{ref}' not found.[/red]")
            sys.exit(1)
        return int(row_id)

    if kind == "section":
        if "#" not in ref:
            console.print(
                "[red]Section ref must be 'doc-key#anchor' (e.g. arch/data-model#overview).[/red]"
            )
            sys.exit(1)
        doc_key, anchor = ref.split("#", 1)
        doc_row = session.execute(
            select(DocumentModel.row_id).where(
                DocumentModel.project_id == project_id,
                DocumentModel.doc_key == doc_key,
            )
        ).scalar_one_or_none()
        if doc_row is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)
        row_id = session.execute(
            select(SectionModel.row_id).where(
                SectionModel.document_id == doc_row,
                SectionModel.anchor == anchor,
            )
        ).scalar_one_or_none()
        if row_id is None:
            console.print(f"[red]Section '{ref}' not found.[/red]")
            sys.exit(1)
        return int(row_id)

    # Fallback: treat ref as raw integer row_id (plan, link, module, …)
    try:
        return int(ref)
    except ValueError:
        console.print(f"[red]For kind='{kind}', ref must be an integer row_id.[/red]")
        sys.exit(1)


@click.group()
def revision() -> None:
    """Inspect and revert revision history."""


# ──────────────────────────────────────────────────────────────────────────────
# revision list
# ──────────────────────────────────────────────────────────────────────────────


@revision.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--kind",
    type=click.Choice(_KIND_CHOICES),
    help="Entity kind (режим сущности, вместе с --ref)",
)
@click.option(
    "--ref",
    help="Entity ref: task_id / doc_key / story_id / doc_key#anchor / row_id",
)
@click.option(
    "--all",
    "all_feed",
    is_flag=True,
    default=False,
    help="Лента ревизий всего проекта (newest-first), без привязки к сущности",
)
@click.option(
    "--since",
    default=None,
    help="ISO-8601: ревизии не раньше момента, напр. 2026-09-26 (только с --all)",
)
@click.option("--author", default=None, help="Фильтр по автору (только с --all)")
@click.option(
    "--entity-kind",
    type=click.Choice(_KIND_CHOICES),
    default=None,
    help="Фильтр по виду сущности (только с --all)",
)
@click.option("--limit", default=20, show_default=True, help="Max revisions to show")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def revision_list(
    ctx: click.Context,
    project: str,
    kind: str | None,
    ref: str | None,
    all_feed: bool,
    since: str | None,
    author: str | None,
    entity_kind: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """List revision history for an entity — or the project feed with --all.

    \b
    Ревизии за сегодня по проекту:
      cod-doc revision list -p X --all --since 2026-09-26 --json
    """
    from cod_doc.domain.entities import EntityKind
    from cod_doc.infra.db import transactional
    from cod_doc.services import revision_service, task_service

    if all_feed:
        if kind or ref:
            raise click.UsageError(
                "--all нельзя сочетать с --kind/--ref: лента проекта не режется по сущности."
            )
    else:
        if since or author or entity_kind:
            raise click.UsageError("--since/--author/--entity-kind работают только вместе с --all.")
        if not (kind and ref):
            raise click.UsageError(
                "Нужны оба --kind и --ref (режим сущности) — или --all для ленты проекта."
            )

    try:
        since_dt = task_service.parse_since(since) if since else None
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from None

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    if all_feed:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            feed = revision_service.list_for_project(
                session,
                project_id,
                limit=limit,
                since=since_dt,
                author=author,
                entity_kind=EntityKind(entity_kind) if entity_kind else None,
            )

        if as_json:
            click.echo(
                _json.dumps(
                    [
                        {
                            "revision_id": r.revision_id,
                            "entity_kind": r.entity_kind.value,
                            "entity_id": r.entity_id,
                            "author": r.author,
                            "at": r.at.isoformat() if r.at else None,
                            "reason": r.reason,
                        }
                        for r in feed
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        if not feed:
            console.print("[dim]No revisions found.[/dim]")
            return

        table = Table(title=f"Revisions — project {project}", show_header=True)
        table.add_column("Revision ID", style="cyan", no_wrap=True, width=28)
        table.add_column("Kind", width=10)
        table.add_column("Entity", width=8)
        table.add_column("Author", width=20)
        table.add_column("At", width=20)
        table.add_column("Reason")
        for r in feed:
            at_str = r.at.isoformat()[:19] if r.at else "—"
            table.add_row(
                r.revision_id,
                r.entity_kind.value,
                str(r.entity_id),
                r.author,
                at_str,
                r.reason or "",
            )
        console.print(table)
        return

    assert kind is not None and ref is not None  # проверено выше (UsageError)
    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        entity_id = _resolve_entity_id(session, kind, ref, project_id)
        revisions = revision_service.list_for_entity(
            session, EntityKind(kind), entity_id, limit=limit
        )

    if as_json:
        click.echo(
            _json.dumps(
                [
                    {
                        "revision_id": r.revision_id,
                        "author": r.author,
                        "at": r.at.isoformat() if r.at else None,
                        "reason": r.reason,
                        "parent_revision_id": r.parent_revision_id,
                        "diff": r.diff,
                    }
                    for r in revisions
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not revisions:
        console.print("[dim]No revisions found.[/dim]")
        return

    table = Table(title=f"Revisions — {kind}:{ref}", show_header=True)
    table.add_column("Revision ID", style="cyan", no_wrap=True, width=28)
    table.add_column("Author", width=20)
    table.add_column("At", width=20)
    table.add_column("Reason")
    for r in revisions:
        at_str = r.at.isoformat()[:19] if r.at else "—"
        table.add_row(r.revision_id, r.author, at_str, r.reason or "")
    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# revision summary
# ──────────────────────────────────────────────────────────────────────────────

_GROUP_BY_CHOICES = ["day", "entity_kind", "author"]


@revision.command("summary")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--since",
    required=True,
    help="ISO-8601: ревизии не раньше момента, напр. 2026-09-26",
)
@click.option(
    "--until",
    default=None,
    help="ISO-8601: ревизии не позже момента (по умолчанию — без верхней границы)",
)
@click.option(
    "--group-by",
    "group_by",
    multiple=True,
    type=click.Choice(_GROUP_BY_CHOICES),
    default=("entity_kind",),
    show_default=True,
    help="Ключи агрегации; повторяйте опцию для нескольких",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def revision_summary(
    ctx: click.Context,
    project: str,
    since: str,
    until: str | None,
    group_by: tuple[str, ...],
    as_json: bool,
) -> None:
    """Агрегаты ревизий (GROUP BY) за период.

    \b
    Ревизии за сегодня по видам сущностей:
      cod-doc revision summary -p X --since 2026-09-26 --json
    Сумма n равна длине `revision list --all` за тот же период.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import revision_service, task_service

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
            rows = revision_service.summarize(
                session, project_id, since=since_dt, until=until_dt, group_by=group_by
            )
        except ValueError as exc:
            raise click.UsageError(str(exc)) from None

    if as_json:
        click.echo(_json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if not rows:
        console.print("[dim]No revisions found.[/dim]")
        return

    table = Table(title=f"Revision summary — project {project}", show_header=True)
    for key in group_by:
        table.add_column(key)
    table.add_column("n", justify="right")
    total = 0
    for row in rows:
        total += int(row["n"])
        table.add_row(*[str(row.get(key) or "—") for key in group_by], str(row["n"]))
    table.add_row("итого", *[""] * (len(group_by) - 1), str(total), style="bold")
    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# revision show
# ──────────────────────────────────────────────────────────────────────────────


@revision.command("show")
@click.argument("revision_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def revision_show(ctx: click.Context, revision_id: str, project: str, as_json: bool) -> None:
    """Show a single revision (including its diff)."""
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models import RevisionModel
    from cod_doc.services import revision_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        _require_project_id(session, project)  # validate project exists
        model = session.execute(
            select(RevisionModel).where(RevisionModel.revision_id == revision_id)
        ).scalar_one_or_none()
        if model is None:
            console.print(f"[red]Revision '{revision_id}' not found.[/red]")
            sys.exit(1)
        r = revision_service._to_domain(model)

    if as_json:
        click.echo(
            _json.dumps(
                {
                    "revision_id": r.revision_id,
                    "entity_kind": r.entity_kind.value,
                    "entity_id": r.entity_id,
                    "author": r.author,
                    "at": r.at.isoformat() if r.at else None,
                    "reason": r.reason,
                    "parent_revision_id": r.parent_revision_id,
                    "commit_sha": r.commit_sha,
                    "diff": r.diff,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console.rule(f"[bold cyan]Revision {r.revision_id}[/bold cyan]")
    console.print(f"  Entity:   {r.entity_kind.value} #{r.entity_id}")
    console.print(f"  Author:   {r.author}")
    console.print(f"  At:       {r.at.isoformat()[:19] if r.at else '—'}")
    console.print(f"  Reason:   {r.reason or '—'}")
    if r.parent_revision_id:
        console.print(f"  Parent:   {r.parent_revision_id}")
    if r.commit_sha:
        console.print(f"  Commit:   {r.commit_sha}")
    console.print()
    console.print("[bold]Diff:[/bold]")
    console.print(r.diff, markup=False, highlight=False)


# ──────────────────────────────────────────────────────────────────────────────
# revision revert
# ──────────────────────────────────────────────────────────────────────────────


@revision.command("revert")
@click.argument("revision_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def revision_revert(ctx: click.Context, revision_id: str, project: str, author: str) -> None:
    """Revert a revision (creates an inverse revision; history is append-only)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import revision_service
    from cod_doc.services.revision_service import RevertNotSupportedError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            pid = _require_project_id(session, project)
            new_rev = revision_service.revert(session, revision_id, author=author, project_id=pid)
    except LookupError:
        console.print(f"[red]Revision '{revision_id}' not found.[/red]")
        sys.exit(1)
    except RevertNotSupportedError as exc:
        console.print(f"[red]Revert not supported: {exc}[/red]")
        sys.exit(1)

    console.print(
        f"[green]✅ Reverted {revision_id}[/green]\n"
        f"   New revision: [cyan]{new_rev.revision_id}[/cyan]"
    )
