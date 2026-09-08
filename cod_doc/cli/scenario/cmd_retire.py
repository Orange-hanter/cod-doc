"""`scenario retire` — mark a scenario no longer applicable."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import scenario

if TYPE_CHECKING:
    from cod_doc.config import Config


@scenario.command("retire")
@click.argument("scenario_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def scenario_retire(
    ctx: click.Context,
    scenario_id: str,
    project: str,
    author: str,
    reason: str | None,
) -> None:
    """Retire a scenario. The row and its id are kept; the id is never reused."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service
    from cod_doc.services.scenario_service import ScenarioNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            retired = scenario_service.retire(
                session,
                project_id=project_id,
                scenario_id=scenario_id,
                author=author,
                reason=reason,
            )
    except ScenarioNotFoundError:
        console.print(f"[red]Scenario '{scenario_id}' not found.[/red]")
        sys.exit(1)

    console.print(f"[green]🗄️  Retired [bold]{retired.scenario_id}[/bold][/green]")
    console.print(
        f"   Re-export to drop its section: cod-doc scenario export "
        f"-p {project} --group {retired.group_key}"
    )
