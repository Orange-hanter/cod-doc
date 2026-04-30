"""CLI commands for user story management: story list/show/create/status/add-criterion/link/coverage."""

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

console = Console()
log = get_logger("cli.story")

_STATUS_ICON = {
    "draft": "✏️",
    "accepted": "✅",
    "delivered": "🚀",
    "deferred": "⏸️",
}

_COVERAGE_ICON = {
    "draft": "✏️",
    "accepted": "✅",
    "in-progress": "🔵",
    "delivered": "🚀",
    "deferred": "⏸️",
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


def _require_project_id(session, project_name: str) -> int:  # type: ignore[no-untyped-def]
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        console.print(f"[red]Project '{project_name}' not in DB. Run 'project add' first.[/red]")
        sys.exit(1)
    return proj.row_id


@click.group()
def story() -> None:
    """Manage user stories (create, status, link, coverage)."""


# ──────────────────────────────────────────────────────────────────────────────
# story list
# ──────────────────────────────────────────────────────────────────────────────


@story.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def story_list(ctx: click.Context, project: str, as_json: bool) -> None:
    """List all user stories for a project."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        stories = story_service.list_for_project(session, project_id)

    if as_json:
        console.print(
            _json.dumps(
                [
                    {
                        "story_id": s.story_id,
                        "persona": s.persona,
                        "narrative": s.narrative,
                        "status": s.status.value,
                        "priority": s.priority.value,
                    }
                    for s in stories
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not stories:
        console.print("[dim]No stories found.[/dim]")
        return

    table = Table(title=f"Stories — {project}", show_header=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", width=12)
    table.add_column("Priority", width=10)
    table.add_column("Persona", width=20)
    table.add_column("Narrative")
    for s in stories:
        icon = _STATUS_ICON.get(s.status.value, "⚪")
        table.add_row(
            s.story_id,
            f"{icon} {s.status.value}",
            s.priority.value,
            s.persona,
            s.narrative[:80] + ("…" if len(s.narrative) > 80 else ""),
        )
    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# story show
# ──────────────────────────────────────────────────────────────────────────────


@story.command("show")
@click.argument("story_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def story_show(ctx: click.Context, story_id: str, project: str, as_json: bool) -> None:
    """Show details of a single story, including acceptance criteria and links."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    # Load story + related data in one transaction, check for None outside it.
    acceptance = []
    links = []
    with transactional(sf) as session:
        s = story_service.get(session, story_id)
        if s is not None:
            acceptance = story_service.list_acceptance(session, story_id)
            links = story_service.list_links(session, story_id)

    if s is None:
        console.print(f"[red]Story '{story_id}' not found.[/red]")
        sys.exit(1)

    if as_json:
        console.print(
            _json.dumps(
                {
                    "story_id": s.story_id,
                    "persona": s.persona,
                    "narrative": s.narrative,
                    "status": s.status.value,
                    "priority": s.priority.value,
                    "acceptance": [
                        {"position": a.position, "criterion": a.criterion, "met": a.met}
                        for a in acceptance
                    ],
                    "links": [
                        {
                            "to_kind": lk.to_kind.value,
                            "to_ref": lk.to_ref,
                            "relation": lk.relation.value,
                        }
                        for lk in links
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    icon = _STATUS_ICON.get(s.status.value, "⚪")
    console.rule(f"[bold cyan]{s.story_id}[/bold cyan]")
    console.print(f"  Status:   {icon} {s.status.value}")
    console.print(f"  Priority: {s.priority.value}")
    console.print(f"  Persona:  {s.persona}")
    console.print(f"\n[bold]Narrative:[/bold]\n  {s.narrative}")

    if acceptance:
        console.print("\n[bold]Acceptance criteria:[/bold]")
        for a in acceptance:
            mark = "[green]✓[/green]" if a.met else "[dim]○[/dim]"
            console.print(f"  {mark} [{a.position}] {a.criterion}")

    if links:
        console.print("\n[bold]Links:[/bold]")
        for lk in links:
            console.print(f"  {lk.relation.value}: {lk.to_kind.value}:{lk.to_ref}")


# ──────────────────────────────────────────────────────────────────────────────
# story create
# ──────────────────────────────────────────────────────────────────────────────


@story.command("create")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--id", "story_id", required=True, help="Story ID (e.g. US-001)")
@click.option("--persona", required=True, help="User persona")
@click.option("--narrative", required=True, help="Story narrative (as a ... I want ... so that ...)")
@click.option(
    "--priority", required=True,
    type=click.Choice(["critical", "high", "medium", "low"]),
)
@click.option(
    "--status",
    type=click.Choice(["draft", "accepted", "delivered", "deferred"]),
    default="draft",
    show_default=True,
)
@click.option(
    "--acceptance", "-a", "acceptance_criteria",
    multiple=True,
    help="Acceptance criterion (repeatable: -a 'crit 1' -a 'crit 2')",
)
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def story_create(
    ctx: click.Context,
    project: str,
    story_id: str,
    persona: str,
    narrative: str,
    priority: str,
    status: str,
    acceptance_criteria: tuple[str, ...],
    author: str,
    reason: str | None,
) -> None:
    """Create a new user story."""
    from cod_doc.domain.entities import Priority, UserStoryStatus
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service
    from cod_doc.services.story_service import StoryAlreadyExistsError
    from cod_doc.services.validation import ValidationError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            s = story_service.create(
                session,
                project_id=project_id,
                story_id=story_id,
                persona=persona,
                narrative=narrative,
                priority=Priority(priority),
                author=author,
                status=UserStoryStatus(status),
                acceptance=list(acceptance_criteria) or None,
                reason=reason,
            )
    except ValidationError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        sys.exit(1)
    except StoryAlreadyExistsError:
        console.print(f"[red]Story '{story_id}' already exists.[/red]")
        sys.exit(1)

    console.print(f"[green]✅ Created story [bold]{s.story_id}[/bold][/green]")
    if acceptance_criteria:
        console.print(f"   Acceptance criteria: {len(acceptance_criteria)}")


# ──────────────────────────────────────────────────────────────────────────────
# story status
# ──────────────────────────────────────────────────────────────────────────────


@story.command("status")
@click.argument("story_id")
@click.argument("new_status", metavar="STATUS",
                type=click.Choice(["draft", "accepted", "delivered", "deferred"]))
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def story_status(
    ctx: click.Context,
    story_id: str,
    new_status: str,
    project: str,
    author: str,
    reason: str | None,
) -> None:
    """Update a story's status."""
    from cod_doc.domain.entities import UserStoryStatus
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service
    from cod_doc.services.story_service import StoryNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            s = story_service.update_status(
                session,
                story_id=story_id,
                new_status=UserStoryStatus(new_status),
                author=author,
                reason=reason,
            )
    except StoryNotFoundError:
        console.print(f"[red]Story '{story_id}' not found.[/red]")
        sys.exit(1)

    icon = _STATUS_ICON.get(s.status.value, "⚪")
    console.print(f"[green]{s.story_id}: {icon} {s.status.value}[/green]")


# ──────────────────────────────────────────────────────────────────────────────
# story add-criterion
# ──────────────────────────────────────────────────────────────────────────────


@story.command("add-criterion")
@click.argument("story_id")
@click.argument("criterion")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def story_add_criterion(
    ctx: click.Context,
    story_id: str,
    criterion: str,
    project: str,
    author: str,
) -> None:
    """Append an acceptance criterion to a story."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service
    from cod_doc.services.story_service import StoryNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            ac = story_service.add_criterion(
                session, story_id=story_id, criterion=criterion, author=author
            )
    except StoryNotFoundError:
        console.print(f"[red]Story '{story_id}' not found.[/red]")
        sys.exit(1)

    console.print(
        f"[green]✅ Added criterion [{ac.position}] to {story_id}: {criterion}[/green]"
    )


# ──────────────────────────────────────────────────────────────────────────────
# story link
# ──────────────────────────────────────────────────────────────────────────────


@story.command("link")
@click.argument("story_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--kind", "to_kind", required=True,
    type=click.Choice(["task", "document", "module"]),
    help="Target entity kind",
)
@click.option("--ref", "to_ref", required=True, help="Target entity ID/key")
@click.option(
    "--relation", required=True,
    type=click.Choice(["implemented_by", "specified_in", "owned_by"]),
)
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def story_link(
    ctx: click.Context,
    story_id: str,
    project: str,
    to_kind: str,
    to_ref: str,
    relation: str,
    author: str,
) -> None:
    """Link a story to a task, document, or module."""
    from cod_doc.domain.entities import StoryLinkKind, StoryRelation
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service
    from cod_doc.services.story_service import BrokenLinkError, StoryNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            lk = story_service.link(
                session,
                story_id=story_id,
                to_kind=StoryLinkKind(to_kind),
                to_ref=to_ref,
                relation=StoryRelation(relation),
                author=author,
            )
    except StoryNotFoundError:
        console.print(f"[red]Story '{story_id}' not found.[/red]")
        sys.exit(1)
    except BrokenLinkError as exc:
        console.print(f"[red]Broken link: {exc}[/red]")
        sys.exit(1)

    console.print(
        f"[green]✅ Linked {story_id} → {lk.relation.value}: {lk.to_kind.value}:{lk.to_ref}[/green]"
    )


# ──────────────────────────────────────────────────────────────────────────────
# story coverage
# ──────────────────────────────────────────────────────────────────────────────


@story.command("coverage")
@click.argument("story_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def story_coverage(ctx: click.Context, story_id: str, project: str, as_json: bool) -> None:
    """Show derived coverage status for a story."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service
    from cod_doc.services.story_service import StoryNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            cov = story_service.coverage(session, story_id)
    except StoryNotFoundError:
        console.print(f"[red]Story '{story_id}' not found.[/red]")
        sys.exit(1)

    if as_json:
        console.print(
            _json.dumps(
                {
                    "story_id": cov.story_id,
                    "status": cov.status.value,
                    "tasks_total": cov.tasks_total,
                    "tasks_done": cov.tasks_done,
                    "tasks_in_progress": cov.tasks_in_progress,
                    "acceptance_total": cov.acceptance_total,
                    "acceptance_met": cov.acceptance_met,
                },
                indent=2,
            )
        )
        return

    icon = _COVERAGE_ICON.get(cov.status.value, "⚪")
    console.rule(f"[bold]Coverage — {cov.story_id}[/bold]")
    console.print(f"  Status:      {icon} {cov.status.value}")
    console.print(
        f"  Tasks:       {cov.tasks_done}/{cov.tasks_total} done"
        + (f", {cov.tasks_in_progress} in-progress" if cov.tasks_in_progress else "")
    )
    console.print(
        f"  Acceptance:  {cov.acceptance_met}/{cov.acceptance_total} met"
    )
