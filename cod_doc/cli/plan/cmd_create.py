"""`plan create` — bootstrap a plan, optionally with seed sections."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import plan

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cod_doc.config import Config


def _sections_from_specs(specs: Sequence[str]) -> list[dict[str, str]]:
    seeded: list[dict[str, str]] = []
    for spec in specs:
        if ":" not in spec:
            raise click.UsageError(f"invalid --section {spec!r}; expected LETTER:TITLE")
        letter, title = spec.split(":", 1)
        letter = letter.strip()
        title = title.strip()
        if not letter or not title:
            raise click.UsageError(f"invalid --section {spec!r}; expected LETTER:TITLE")
        seeded.append({"letter": letter, "title": title})
    return seeded


@plan.command("create")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--scope", required=True, help="Plan scope identifier (unique)")
@click.option("--principle", default="from-rfc", show_default=True)
@click.option("--author", default="human:cli", show_default=True)
@click.option(
    "--section",
    "section_specs",
    multiple=True,
    metavar="LETTER:TITLE",
    help="Seed a section (repeatable). Split on the first colon.",
)
@click.pass_context
def plan_create(
    ctx: click.Context,
    project: str,
    scope: str,
    principle: str,
    author: str,
    section_specs: tuple[str, ...],
) -> None:
    """Create a plan, optionally with initial sections."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_write_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    sections = _sections_from_specs(section_specs)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            created = plan_write_service.create_plan(
                session,
                project_id=project_id,
                scope=scope,
                principle=principle,
                sections=sections,
                author=author,
            )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    n_sec = len(created["sections"])
    console.print(
        f"[green]✓ Created[/green] plan [cyan]{created['scope']}[/cyan] "
        f"(id={created['plan_id']}, sections={n_sec})"
    )
