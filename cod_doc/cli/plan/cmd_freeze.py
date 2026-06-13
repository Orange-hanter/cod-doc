"""`plan freeze` — snapshot a plan's projection into a frozen document (COD-052)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_plan_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("freeze")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="human:cli", help="Author recorded on the snapshot")
@click.option("--reason", default=None, help="Optional reason for the freeze")
@click.pass_context
def plan_freeze(
    ctx: click.Context,
    plan_scope: str,
    project: str,
    author: str,
    reason: str | None,
) -> None:
    """Snapshot a plan's current projection into an immutable frozen document.

    Renders Progress Overview / Next Batch / Dependency Graph and stores them as
    an EXECUTION_LOG document keyed ``frozen/<scope>/<UTC timestamp>``. Each call
    appends a new snapshot.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        plan_id = _require_plan_id(session, plan_scope)
        doc = plan_service.freeze_projection(session, plan_id, author=author, reason=reason)
        key = doc.doc_key

    console.print(f"[green]✓ Frozen[/green] {plan_scope} → [cyan]{key}[/cyan]")
