"""`adr sync` — re-sync an ADR body from its markdown projection (ADO-168)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ._common import console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config

_FIELDS = ("title", "decided_at", "context", "decision", "alternatives", "consequences")


@adr.command("sync")
@click.argument("adr_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--title", default=None, help="Title as written in the projection")
@click.option("--decided-at", default=None, help="ISO date (YYYY-MM-DD)")
@click.option("--context", default=None, help="Context section")
@click.option("--decision", default=None, help="Decision section")
@click.option("--alternatives", default=None, help="Alternatives section")
@click.option("--consequences", default=None, help="Consequences section")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def adr_sync(
    ctx: click.Context,
    adr_id: str,
    project: str,
    title: str | None,
    decided_at: str | None,
    context: str | None,
    decision: str | None,
    alternatives: str | None,
    consequences: str | None,
    author: str,
    reason: str | None,
) -> None:
    """Re-sync ADR_ID's body from markdown, ignoring the status gate.

    Use when the markdown projection is the source and the DB row fell
    behind it — a corrected citation, a section the parser missed. Unlike
    ``adr update`` this works on ACCEPTED and on terminal ADRs, because the
    decision is not being amended: only its record catches up.

    Status is deliberately not settable here. Moving between statuses stays
    ``adr update`` (from PROPOSED), ``adr deprecate`` or ``adr supersede``.

    Passing no field is an error — that is a typo, not a no-op.
    """
    from datetime import date

    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service
    from cod_doc.services.adr_service import ADRNotFoundError

    supplied = {
        "title": title,
        "decided_at": decided_at,
        "context": context,
        "decision": decision,
        "alternatives": alternatives,
        "consequences": consequences,
    }
    if all(v is None for v in supplied.values()):
        console.print(
            "[red]Nothing to sync: pass at least one of "
            f"{', '.join('--' + f.replace('_', '-') for f in _FIELDS)}.[/red]"
        )
        raise SystemExit(2)

    parsed_date: date | None = None
    if decided_at is not None:
        try:
            parsed_date = date.fromisoformat(decided_at)
        except ValueError as exc:
            console.print(f"[red]--decided-at must be YYYY-MM-DD: {exc}[/red]")
            raise SystemExit(2) from exc

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            before = adr_service.get(session, project_id, adr_id)
            if before is None:
                raise ADRNotFoundError(f"ADR {adr_id} not found in project {project!r}")
            status = before.status
            was = {f: getattr(before, f) for f in _FIELDS}
            row = adr_service.sync_body(
                session,
                project_id=project_id,
                adr_id=adr_id,
                title=title,
                decided_at=parsed_date,
                context=context,
                decision=decision,
                alternatives=alternatives,
                consequences=consequences,
                author=author,
                reason=reason,
            )
            changed = [f for f in _FIELDS if getattr(row, f) != was[f]]
    except ADRNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    if not changed:
        console.print(f"[dim]{adr_id}: projection already matches the DB — nothing written.[/dim]")
        return
    console.print(
        f"🔄 [cyan]{adr_id}[/cyan] body synced from projection "
        f"([dim]status {status} unchanged[/dim]): {', '.join(changed)}"
    )
