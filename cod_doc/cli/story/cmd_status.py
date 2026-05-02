"""`story status` — update a story's status."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _STATUS_ICON, _make_session, console
from ._group import story

if TYPE_CHECKING:
    from cod_doc.config import Config


@story.command("status")
@click.argument("story_id")
@click.argument(
    "new_status",
    metavar="STATUS",
    type=click.Choice(["draft", "accepted", "delivered", "deferred"]),
)
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
