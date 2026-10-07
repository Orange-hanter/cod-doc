"""`adr topic …` и `adr set-topic` — полки реестра ADR (ARG-008, RFC 34 §3.4)."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.markup import escape

from ._common import console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config


@adr.group("topic")
def adr_topic() -> None:
    """ADR topics («shelves»): what a decision is about. Order = display order."""


@adr_topic.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def topic_list(ctx: click.Context, project: str, as_json: bool) -> None:
    """List topics in display order with how many ADRs each holds."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_topic_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with transactional(sf) as session:
        project_id = require_project_id(session, project)
        counts = adr_topic_service.adr_counts(session, project_id)
        rows = [
            adr_topic_service.topic_to_dict(t, adr_count=counts.get(t.row_id, 0))
            for t in adr_topic_service.list_for_project(session, project_id)
        ]
    if as_json:
        click.echo(_json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        console.print("[dim]No topics yet: cod-doc adr topic create -p <project> NAME[/dim]")
        return
    for r in rows:
        console.print(
            f"{r['position']:>2}  [bold]{escape(str(r['name']))}[/bold]  [dim]{r['adr_count']} ADR[/dim]"
        )
        if r["includes"]:
            console.print(f"    in:  {escape(str(r['includes']))}")
        if r["excludes"]:
            console.print(f"    out: {escape(str(r['excludes']))}")


@adr_topic.command("create")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("name")
@click.option("--includes", default="", help="What belongs on this topic")
@click.option("--excludes", default="", help="What goes elsewhere (and where)")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def topic_create(
    ctx: click.Context, project: str, name: str, includes: str, excludes: str, author: str
) -> None:
    """Create topic NAME at the end of the list."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_topic_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            topic = adr_topic_service.create(
                session,
                project_id=project_id,
                name=name,
                includes=includes,
                excludes=excludes,
                author=author,
            )
            position = topic.position
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(2) from exc
    console.print(f"🗂️  topic [cyan]{escape(name)}[/cyan] created at position {position}")


@adr_topic.command("update")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("name")
@click.option("--rename", "new_name", default=None, help="New name")
@click.option("--includes", default=None)
@click.option("--excludes", default=None)
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def topic_update(
    ctx: click.Context,
    project: str,
    name: str,
    new_name: str | None,
    includes: str | None,
    excludes: str | None,
    author: str,
) -> None:
    """Rename topic NAME or edit its includes/excludes."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_topic_service
    from cod_doc.services.adr_topic_service import ADRTopicNotFoundError

    if new_name is None and includes is None and excludes is None:
        console.print("[red]Nothing to update: pass --rename, --includes or --excludes.[/red]")
        raise SystemExit(2)
    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            adr_topic_service.update(
                session,
                project_id=project_id,
                name=name,
                new_name=new_name,
                includes=includes,
                excludes=excludes,
                author=author,
            )
    except ADRTopicNotFoundError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(1) from exc
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(2) from exc
    console.print(f"🗂️  topic [cyan]{escape(new_name or name)}[/cyan] updated")


@adr_topic.command("move")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("name")
@click.argument("position", type=int)
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def topic_move(ctx: click.Context, project: str, name: str, position: int, author: str) -> None:
    """Move topic NAME to POSITION (0-based); the others shift."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_topic_service
    from cod_doc.services.adr_topic_service import ADRTopicNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            topic = adr_topic_service.move(
                session, project_id=project_id, name=name, position=position, author=author
            )
            final = topic.position
    except ADRTopicNotFoundError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(1) from exc
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(2) from exc
    console.print(f"🗂️  topic [cyan]{escape(name)}[/cyan] is now at position {final}")


@adr_topic.command("delete")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("name")
@click.option("--author", default="cli", show_default=True)
@click.option("--yes", is_flag=True, default=False, help="Do not ask")
@click.pass_context
def topic_delete(ctx: click.Context, project: str, name: str, author: str, yes: bool) -> None:
    """Delete topic NAME. Its ADRs move to «no topic»."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_topic_service
    from cod_doc.services.adr_topic_service import ADRTopicNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            topic = adr_topic_service.require(session, project_id, name)
            held = adr_topic_service.adr_counts(session, project_id).get(topic.row_id, 0)
            if held and not yes:
                click.confirm(
                    f"{held} ADR(s) on «{name}» will move to «no topic». Delete?", abort=True
                )
            moved = adr_topic_service.delete(
                session, project_id=project_id, name=name, author=author
            )
    except ADRTopicNotFoundError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(1) from exc
    console.print(f"🗑️  topic [cyan]{escape(name)}[/cyan] deleted; {moved} ADR(s) now have no topic")


@adr.command("set-topic")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("adr_id")
@click.argument("topic", required=False)
@click.option("--clear", is_flag=True, default=False, help="Move the ADR to «no topic»")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def adr_set_topic(
    ctx: click.Context,
    project: str,
    adr_id: str,
    topic: str | None,
    clear: bool,
    author: str,
) -> None:
    """Put ADR_ID on TOPIC (any status, including accepted). --clear removes it."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service
    from cod_doc.services.adr_service import ADRNotFoundError
    from cod_doc.services.adr_topic_service import ADRTopicNotFoundError

    # Пустой TOPIC — не имя полки: без --clear это ошибка, как и отсутствие обоих.
    topic = (topic or "").strip() or None
    if clear == (topic is not None):
        console.print("[red]Pass exactly one of TOPIC or --clear.[/red]")
        raise SystemExit(2)
    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    try:
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            adr_service.set_topic(
                session, project_id=project_id, adr_id=adr_id, topic=topic, author=author
            )
    except (ADRNotFoundError, ADRTopicNotFoundError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(1) from exc
    console.print(f"🗂️  [cyan]{escape(adr_id)}[/cyan] → {escape(topic or 'no topic')}")
