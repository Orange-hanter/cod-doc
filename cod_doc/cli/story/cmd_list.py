"""`story list` — list all user stories for a project."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _STATUS_ICON, _make_session, _require_project_id, console
from ._group import story

if TYPE_CHECKING:
    from cod_doc.config import Config


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
