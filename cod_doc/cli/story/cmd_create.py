"""`story create` — create a new user story."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import story

if TYPE_CHECKING:
    from cod_doc.config import Config


@story.command("create")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--id", "story_id", required=True, help="Story ID (e.g. US-001)")
@click.option("--persona", required=True, help="User persona")
@click.option(
    "--narrative", required=True, help="Story narrative (as a ... I want ... so that ...)"
)
@click.option(
    "--priority",
    required=True,
    type=click.Choice(["critical", "high", "medium", "low"]),
)
@click.option(
    "--status",
    type=click.Choice(["draft", "accepted", "delivered", "deferred"]),
    default="draft",
    show_default=True,
)
@click.option(
    "--acceptance",
    "-a",
    "acceptance_criteria",
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
