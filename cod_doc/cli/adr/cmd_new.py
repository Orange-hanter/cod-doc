"""`adr new` — create a new Architecture Decision Record."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import click

from ._common import STATUS_ICON, console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config


@adr.command("new")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--title", "-t", required=True, help="ADR title")
@click.option(
    "--status",
    type=click.Choice(["proposed", "accepted", "superseded", "deprecated", "rejected"]),
    default="proposed",
    show_default=True,
)
@click.option("--decided-at", help="ISO date (YYYY-MM-DD)", default=None)
@click.option("--context", help="Context paragraph", default=None)
@click.option("--decision", help="The decision itself", default=None)
@click.option("--alternatives", help="Alternatives considered", default=None)
@click.option("--consequences", help="Consequences (+ pros / − cons)", default=None)
@click.option("--adr-id", help="Explicit ADR-NNN id (auto if omitted)", default=None)
@click.option("--author", default="human", show_default=True)
@click.pass_context
def adr_new(
    ctx: click.Context,
    project: str,
    title: str,
    status: str,
    decided_at: str | None,
    context: str | None,
    decision: str | None,
    alternatives: str | None,
    consequences: str | None,
    adr_id: str | None,
    author: str,
) -> None:
    """Create a new ADR (auto-allocates ADR-NNN if --adr-id omitted)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service
    from cod_doc.services.adr_service import ADRAlreadyExistsError

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)

    parsed_date = date.fromisoformat(decided_at) if decided_at else None

    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            row = adr_service.create(
                session, project_id=project_id, title=title, status=status,
                decided_at=parsed_date, context=context, decision=decision,
                alternatives=alternatives, consequences=consequences,
                adr_id=adr_id, author=author,
            )
            new_id = row.adr_id
    except ADRAlreadyExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    icon = STATUS_ICON.get(status, "•")
    console.print(f"{icon} Created [cyan]{new_id}[/cyan] — {title} ([dim]{status}[/dim])")
