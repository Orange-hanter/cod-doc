"""`story section` / `story set-section` — реестр секций и привязка истории."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import story

if TYPE_CHECKING:
    from cod_doc.config import Config


@story.group("section")
def story_section() -> None:
    """Manage story sections (product modules)."""


@story_section.command("add")
@click.argument("key")
@click.argument("title")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--position", type=int, default=None, help="Order; defaults to end of list")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def section_add(
    ctx: click.Context,
    key: str,
    title: str,
    project: str,
    position: int | None,
    author: str,
) -> None:
    """Create a story section. KEY is a slug: lowercase, digits, dashes."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service
    from cod_doc.services.story_service import SectionAlreadyExistsError
    from cod_doc.services.validation import ValidationError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            section = story_service.create_section(
                session,
                project_id=project_id,
                key=key,
                title=title,
                position=position,
                author=author,
            )
    except ValidationError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        sys.exit(1)
    except SectionAlreadyExistsError:
        console.print(f"[red]Section '{key}' already exists in {project}.[/red]")
        sys.exit(1)

    console.print(
        f"[green]✅ Created section [bold]{section.key}[/bold] "
        f"«{section.title}» at position {section.position}[/green]"
    )


@story_section.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def section_list(ctx: click.Context, project: str, as_json: bool) -> None:
    """List story sections with their story counts."""
    import json as json_mod

    from rich.table import Table

    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        sections = story_service.list_sections(session, project_id)
        all_stories = story_service.list_for_project(session, project_id)

    counts: dict[int | None, int] = {}
    for st in all_stories:
        counts[st.section_id] = counts.get(st.section_id, 0) + 1
    unsectioned = counts.get(None, 0)

    if as_json:
        console.print(
            json_mod.dumps(
                [
                    {
                        "key": sec.key,
                        "title": sec.title,
                        "position": sec.position,
                        "stories": counts.get(sec.row_id, 0),
                    }
                    for sec in sections
                ]
                + [{"key": None, "title": "No section", "position": None, "stories": unsectioned}],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    table = Table(title=f"Story sections — {project}")
    table.add_column("Key", style="cyan")
    table.add_column("Title")
    table.add_column("Pos", justify="right")
    table.add_column("Stories", justify="right")
    for sec in sections:
        table.add_row(sec.key, sec.title, str(sec.position), str(counts.get(sec.row_id, 0)))
    if unsectioned:
        table.add_row("[dim]—[/dim]", "[dim]No section[/dim]", "", str(unsectioned))
    console.print(table)


@story.command("set-section")
@click.argument("story_id")
@click.argument("key", required=False)
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--clear", is_flag=True, help="Detach the story from its section")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def story_set_section(
    ctx: click.Context,
    story_id: str,
    key: str | None,
    project: str,
    clear: bool,
    author: str,
    reason: str | None,
) -> None:
    """Assign STORY_ID to section KEY (or --clear to detach)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import story_service
    from cod_doc.services.story_service import SectionNotFoundError, StoryNotFoundError

    if clear and key is not None:
        # Молча проигнорировать KEY нельзя: намерение противоречиво, и тихая
        # отвязка вместо привязки — не то, чего ждал набравший обе формы.
        console.print(f"[red]--clear конфликтует с KEY '{key}'. Оставь что-то одно.[/red]")
        sys.exit(1)
    if clear:
        key = None
    elif key is None:
        console.print("[red]Pass a section KEY or use --clear.[/red]")
        sys.exit(1)

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            section = story_service.assign_section(
                session, story_id=story_id, key=key, author=author, reason=reason
            )
    except StoryNotFoundError:
        console.print(f"[red]Story '{story_id}' not found.[/red]")
        sys.exit(1)
    except SectionNotFoundError:
        console.print(
            f"[red]Section '{key}' not found in {project}. "
            f"Create it first: cod-doc story section add {key} '<Title>' -p {project}[/red]"
        )
        sys.exit(1)

    if section is None:
        console.print(f"[green]✅ {story_id} detached from its section[/green]")
    else:
        console.print(
            f"[green]✅ {story_id} → [bold]{section.key}[/bold] «{section.title}»[/green]"
        )
