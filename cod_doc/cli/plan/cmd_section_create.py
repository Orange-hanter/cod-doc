"""`plan section-create` — append a section to an existing plan."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_plan_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("section-create")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--letter", required=True, help="Section letter (e.g. A)")
@click.option("--title", required=True, help="Section title")
@click.option("--slug", default=None, help="Defaults to the title")
@click.option("--position", type=int, default=None, help="Defaults to append at the tail")
@click.option("--author", default="human:cli", show_default=True)
@click.pass_context
def plan_section_create(
    ctx: click.Context,
    plan_scope: str,
    project: str,
    letter: str,
    title: str,
    slug: str | None,
    position: int | None,
    author: str,
) -> None:
    """Append a section to an existing plan."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_write_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            plan_id = _require_plan_id(session, plan_scope)
            sec = plan_write_service.add_section(
                session,
                plan_id=plan_id,
                letter=letter,
                title=title,
                slug=slug,
                position=position,
                author=author,
            )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(
        f"[green]✓ Section[/green] [cyan]{sec['letter']}[/cyan]: {sec['title']} "
        f"(id={sec['section_id']}, position={sec['position']})"
    )
