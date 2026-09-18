"""`story set-criterion` — отметить критерий приёмки выполненным (ADO-145).

Сервис ``set_criterion_met`` существовал с COD-014, но не имел ни одного
вызова: ни CLI, ни MCP, ни web. Из-за этого ``met`` всегда оставался 0, а
``coverage`` не мог вывести ``delivered`` — ветка была структурно недостижима.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, console
from ._group import story

if TYPE_CHECKING:
    from cod_doc.config import Config


@story.command("set-criterion")
@click.argument("story_id")
@click.argument("position", type=int)
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--met/--not-met", default=True, show_default=True, help="Mark met or unmet")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def story_set_criterion(
    ctx: click.Context,
    story_id: str,
    position: int,
    project: str,
    met: bool,
    author: str,
    reason: str | None,
) -> None:
    """Mark acceptance criterion POSITION of STORY_ID as met (or --not-met)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service
    from cod_doc.services.story_service import AcceptanceNotFoundError, StoryNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            ac = story_service.set_criterion_met(
                session,
                story_id=story_id,
                position=position,
                met=met,
                author=author,
                reason=reason,
            )
    except StoryNotFoundError:
        console.print(f"[red]Story '{story_id}' not found.[/red]")
        sys.exit(1)
    except AcceptanceNotFoundError:
        console.print(f"[red]Story '{story_id}' has no criterion at position {position}.[/red]")
        sys.exit(1)

    mark = "✓ met" if ac.met else "○ not met"
    console.print(f"[green]✅ {story_id}[{ac.position}] → {mark}[/green]")
    console.print(f"   {ac.criterion}")
