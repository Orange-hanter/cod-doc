"""`adr deprecate` — retire an ADR (PROPOSED or ACCEPTED → DEPRECATED)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ._common import console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config


@adr.command("deprecate")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("adr_id")
@click.option("--reason", default=None, help="Why deprecated")
@click.pass_context
def adr_deprecate(
    ctx: click.Context,
    project: str,
    adr_id: str,
    reason: str | None,
) -> None:
    """Transition ADR_ID to DEPRECATED status.

    Use when a decision is retired without being replaced by a new one.
    For replacement-style retirement, use ``adr supersede`` instead.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service
    from cod_doc.services.adr_service import ADRNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            adr_service.deprecate(
                session, project_id=project_id, adr_id=adr_id,
                reason=reason,
            )
    except ADRNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc

    console.print(
        f"⚠️  [yellow]{adr_id}[/yellow] is now [bold]DEPRECATED[/bold]"
        + (f" — [dim]{reason}[/dim]" if reason else "")
    )
