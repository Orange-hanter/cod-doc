"""`adr relate` / `adr unrelate` — связи «уточняет» и «опирается на» (ARG-001)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ._common import console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config

# Дублирует ``ADR_RELATION_KINDS``: импорт моделей потянул бы SQLAlchemy
# в старт CLI (ADO-179). Расхождение ловит тест на help.
_KINDS = ("amends", "depends_on")
_KIND_LABEL = {"amends": "amends", "depends_on": "depends on"}


@adr.command("relate")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("from_adr_id")
@click.argument("to_adr_id")
@click.option(
    "--kind",
    required=True,
    type=click.Choice(_KINDS),
    help="amends: both stay in force; depends_on: FROM cannot be accepted before TO",
)
@click.option("--reason", default=None, help="Why — a quote from the ADR body works best")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def adr_relate(
    ctx: click.Context,
    project: str,
    from_adr_id: str,
    to_adr_id: str,
    kind: str,
    reason: str | None,
    author: str,
) -> None:
    """Record that FROM_ADR_ID amends or depends on TO_ADR_ID.

    Unlike `adr supersede`, neither status changes, and the relation can be
    recorded from any status of FROM_ADR_ID, including accepted. A repeated
    relation, a self-loop or a cycle within one kind is an error.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service
    from cod_doc.services.adr_service import ADRNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            adr_service.relate(
                session,
                project_id=project_id,
                from_adr_id=from_adr_id,
                to_adr_id=to_adr_id,
                kind=kind,
                reason=reason,
                author=author,
            )
    except ADRNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc

    console.print(
        f"🔗 [cyan]{from_adr_id}[/cyan] {_KIND_LABEL[kind]} [yellow]{to_adr_id}[/yellow]"
        + (f" — [dim]{reason}[/dim]" if reason else "")
    )


@adr.command("unrelate")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("from_adr_id")
@click.argument("to_adr_id")
@click.option("--kind", required=True, type=click.Choice(_KINDS))
@click.option("--reason", default=None, help="Why the relation is removed")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def adr_unrelate(
    ctx: click.Context,
    project: str,
    from_adr_id: str,
    to_adr_id: str,
    kind: str,
    reason: str | None,
    author: str,
) -> None:
    """Remove a relation recorded by `adr relate`. A missing relation is an error."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service
    from cod_doc.services.adr_service import ADRNotFoundError, ADRRelationNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            adr_service.unrelate(
                session,
                project_id=project_id,
                from_adr_id=from_adr_id,
                to_adr_id=to_adr_id,
                kind=kind,
                reason=reason,
                author=author,
            )
    except (ADRNotFoundError, ADRRelationNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    console.print(
        f"✂️  [cyan]{from_adr_id}[/cyan] no longer {_KIND_LABEL[kind]} [yellow]{to_adr_id}[/yellow]"
    )
