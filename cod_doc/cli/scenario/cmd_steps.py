"""`scenario steps` — replace the ordered steps of one scenario."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import scenario

if TYPE_CHECKING:
    from cod_doc.config import Config


@scenario.command("steps")
@click.argument("scenario_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--step",
    "steps",
    multiple=True,
    required=True,
    help="One action per step (repeatable, in order). Replaces all existing steps.",
)
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def scenario_steps(
    ctx: click.Context,
    scenario_id: str,
    project: str,
    steps: tuple[str, ...],
    author: str,
    reason: str | None,
) -> None:
    """Replace every step of the scenario, renumbering from one."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service
    from cod_doc.services.scenario_service import ScenarioNotFoundError
    from cod_doc.services.validation import ValidationError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            saved = scenario_service.set_steps(
                session,
                project_id=project_id,
                scenario_id=scenario_id,
                steps=list(steps),
                author=author,
                reason=reason,
            )
    except ValidationError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        sys.exit(1)
    except ScenarioNotFoundError:
        console.print(f"[red]Scenario '{scenario_id}' not found.[/red]")
        sys.exit(1)

    console.print(f"[green]✅ {scenario_id}: {len(saved)} step(s) set[/green]")
