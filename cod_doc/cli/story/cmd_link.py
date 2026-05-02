"""`story link` — link a story to a task / document / module."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, console
from ._group import story

if TYPE_CHECKING:
    from cod_doc.config import Config


@story.command("link")
@click.argument("story_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--kind",
    "to_kind",
    required=True,
    type=click.Choice(["task", "document", "module"]),
    help="Target entity kind",
)
@click.option("--ref", "to_ref", required=True, help="Target entity ID/key")
@click.option(
    "--relation",
    required=True,
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
