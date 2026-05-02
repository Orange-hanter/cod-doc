"""`story add-criterion` — append acceptance criterion to a story."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, console
from ._group import story

if TYPE_CHECKING:
    from cod_doc.config import Config


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

    console.print(f"[green]✅ Added criterion [{ac.position}] to {story_id}: {criterion}[/green]")
