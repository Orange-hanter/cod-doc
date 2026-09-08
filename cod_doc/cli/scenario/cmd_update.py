"""`scenario update` — patch fields of one scenario."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import KIND_CHOICES, _make_session, _require_project_id, console
from ._group import scenario

if TYPE_CHECKING:
    from cod_doc.config import Config

# `retired` is reached through `scenario retire`, which is explicit about the
# fact that the id is kept. Coverage verdicts are not offered at all: they are
# RFC 24 §9 evidence, not a claim status.
_UPDATABLE_STATUSES = ["draft", "confirmed"]


@scenario.command("update")
@click.argument("scenario_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--title", default=None)
@click.option("--kind", default=None, type=click.Choice(KIND_CHOICES))
@click.option("--precondition", "preconditions", default=None)
@click.option("--expected", default=None)
@click.option("--notes", default=None)
@click.option("--status", default=None, type=click.Choice(_UPDATABLE_STATUSES))
@click.option("--section-anchor", default=None)
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def scenario_update(
    ctx: click.Context,
    scenario_id: str,
    project: str,
    title: str | None,
    kind: str | None,
    preconditions: str | None,
    expected: str | None,
    notes: str | None,
    status: str | None,
    section_anchor: str | None,
    author: str,
    reason: str | None,
) -> None:
    """Patch the given fields; everything else is left alone."""
    from cod_doc.domain.entities import ScenarioKind, ScenarioStatus
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service, validation
    from cod_doc.services.scenario_service import ScenarioNotFoundError
    from cod_doc.services.validation import ValidationError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        # Validate before coercing, so SCV-003 explains itself.
        if status is not None:
            validation.validate_scenario_status(status)
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            updated = scenario_service.update(
                session,
                project_id=project_id,
                scenario_id=scenario_id,
                author=author,
                title=title,
                kind=ScenarioKind(kind) if kind else None,
                preconditions=preconditions,
                expected=expected,
                notes=notes,
                status=ScenarioStatus(status) if status else None,
                section_anchor=section_anchor,
                reason=reason,
            )
    except ValidationError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        sys.exit(1)
    except ScenarioNotFoundError:
        console.print(f"[red]Scenario '{scenario_id}' not found.[/red]")
        sys.exit(1)

    console.print(f"[green]✅ Updated [bold]{updated.scenario_id}[/bold][/green]")
