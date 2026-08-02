"""`adr supersede` — record a "ADR-N replaces ADR-M" relationship."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ._common import console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config


@adr.command("supersede")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("superseding_adr_id")
@click.argument("superseded_adr_id")
@click.option("--reason", default=None, help="Why the replacement")
@click.pass_context
def adr_supersede(
    ctx: click.Context,
    project: str,
    superseding_adr_id: str,
    superseded_adr_id: str,
    reason: str | None,
) -> None:
    """Mark SUPERSEDED_ADR_ID as replaced by SUPERSEDING_ADR_ID.

    Creates a DAG edge AND auto-flips the old ADR's status to 'superseded'
    in a single transaction. Idempotent: re-running with the same pair
    won't duplicate the edge.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service
    from cod_doc.services.adr_service import ADRNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            adr_service.supersede(
                session,
                project_id=project_id,
                superseding_adr_id=superseding_adr_id,
                superseded_adr_id=superseded_adr_id,
                reason=reason,
            )
    except ADRNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc

    console.print(
        f"🔁 [cyan]{superseding_adr_id}[/cyan] now supersedes "
        f"[yellow]{superseded_adr_id}[/yellow]" + (f" — [dim]{reason}[/dim]" if reason else "")
    )
