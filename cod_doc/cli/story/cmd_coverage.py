"""`story coverage` — show derived coverage status for a story."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _COVERAGE_ICON, _make_session, console
from ._group import story

if TYPE_CHECKING:
    from cod_doc.config import Config


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
    console.print(f"  Acceptance:  {cov.acceptance_met}/{cov.acceptance_total} met")
