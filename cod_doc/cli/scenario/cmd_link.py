"""`scenario link` / `scenario unlink` — attach and detach scenario edges."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import (
    LINK_KIND_CHOICES,
    RELATION_CHOICES,
    _make_session,
    _require_project_id,
    console,
)
from ._group import scenario

if TYPE_CHECKING:
    from cod_doc.config import Config


@scenario.command("link")
@click.argument("scenario_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--to-kind", required=True, type=click.Choice(LINK_KIND_CHOICES))
@click.option("--to-ref", required=True, help="task id, story id, doc key, or STORY-ID#position")
@click.option("--relation", required=True, type=click.Choice(RELATION_CHOICES))
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def scenario_link(
    ctx: click.Context,
    scenario_id: str,
    project: str,
    to_kind: str,
    to_ref: str,
    relation: str,
    author: str,
    reason: str | None,
) -> None:
    """Attach an edge. Targets are checked for shape, not existence."""
    from cod_doc.domain.entities import ScenarioLinkKind, ScenarioRelation
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service
    from cod_doc.services.scenario_service import ScenarioNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            scenario_service.link(
                session,
                project_id=project_id,
                scenario_id=scenario_id,
                to_kind=ScenarioLinkKind(to_kind),
                to_ref=to_ref,
                relation=ScenarioRelation(relation),
                author=author,
                reason=reason,
            )
    except ScenarioNotFoundError:
        console.print(f"[red]Scenario '{scenario_id}' not found.[/red]")
        sys.exit(1)

    console.print(f"[green]🔗 {scenario_id} {relation} → {to_kind}:{to_ref}[/green]")


@scenario.command("unlink")
@click.argument("scenario_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--to-kind", required=True, type=click.Choice(LINK_KIND_CHOICES))
@click.option("--to-ref", required=True)
@click.option("--relation", required=True, type=click.Choice(RELATION_CHOICES))
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def scenario_unlink(
    ctx: click.Context,
    scenario_id: str,
    project: str,
    to_kind: str,
    to_ref: str,
    relation: str,
    author: str,
    reason: str | None,
) -> None:
    """Detach an edge."""
    from cod_doc.domain.entities import ScenarioLinkKind, ScenarioRelation
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service
    from cod_doc.services.scenario_service import ScenarioNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            removed = scenario_service.unlink(
                session,
                project_id=project_id,
                scenario_id=scenario_id,
                to_kind=ScenarioLinkKind(to_kind),
                to_ref=to_ref,
                relation=ScenarioRelation(relation),
                author=author,
                reason=reason,
            )
    except ScenarioNotFoundError:
        console.print(f"[red]Scenario '{scenario_id}' not found.[/red]")
        sys.exit(1)

    if not removed:
        console.print("[yellow]No such edge; nothing removed.[/yellow]")
        return
    console.print(f"[green]⊘ {scenario_id} {relation} → {to_kind}:{to_ref} removed[/green]")
