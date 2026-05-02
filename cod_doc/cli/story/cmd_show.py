"""`story show` — details of a single story (acceptance + links)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _STATUS_ICON, _make_session, console
from ._group import story

if TYPE_CHECKING:
    from cod_doc.config import Config


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
