"""`scenario new` — author one test scenario."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import KIND_CHOICES, _make_session, _require_project_id, console
from ._group import scenario

if TYPE_CHECKING:
    from cod_doc.config import Config


@scenario.command("new")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--title", required=True, help="What the scenario demonstrates")
@click.option("--kind", required=True, type=click.Choice(KIND_CHOICES), help="RFC 24 §9 shape")
@click.option("--group", "group_key", default=None, help="Group key (defaults to the doc basename)")
@click.option("--doc-key", default=None, help="Capability document this scenario is anchored to")
@click.option("--section-anchor", default=None, help="Anchor of the section stating the obligation")
@click.option("--precondition", "preconditions", required=True, help="State before the steps run")
@click.option(
    "--step",
    "steps",
    multiple=True,
    required=True,
    help="One action per step (repeatable, in order)",
)
@click.option("--expected", required=True, help="The single observable outcome")
@click.option("--notes", default=None)
@click.option("--id", "scenario_id", default=None, help="Explicit SCN-NNN (default: next free)")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def scenario_new(
    ctx: click.Context,
    project: str,
    title: str,
    kind: str,
    group_key: str | None,
    doc_key: str | None,
    section_anchor: str | None,
    preconditions: str,
    steps: tuple[str, ...],
    expected: str,
    notes: str | None,
    scenario_id: str | None,
    author: str,
    reason: str | None,
) -> None:
    """Author one test scenario.

    Either --group or --doc-key is required; with --doc-key the group defaults
    to the document's basename, which is also the projection filename.
    """
    from cod_doc.domain.entities import ScenarioKind
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service
    from cod_doc.services.scenario_service import ScenarioAlreadyExistsError
    from cod_doc.services.validation import ValidationError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            created = scenario_service.create(
                session,
                project_id=project_id,
                title=title,
                kind=ScenarioKind(kind),
                group_key=group_key,
                doc_key=doc_key,
                section_anchor=section_anchor,
                preconditions=preconditions,
                expected=expected,
                steps=list(steps),
                notes=notes,
                scenario_id=scenario_id,
                author=author,
                reason=reason,
            )
    except ValidationError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        sys.exit(1)
    except ScenarioAlreadyExistsError as exc:
        console.print(f"[red]Scenario '{exc}' already exists.[/red]")
        sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(
        f"[green]✅ Created [bold]{created.scenario_id}[/bold][/green] "
        f"({created.kind.value}, group '{created.group_key}', {len(steps)} step(s))"
    )
    console.print(
        f"   Export it with: cod-doc scenario export -p {project} --group {created.group_key}"
    )
