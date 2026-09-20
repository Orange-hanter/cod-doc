"""`doc tree health` — пробелы в наполненности разделов.

Зеркало MCP-семейства ``doc_node_health_*``. Дерево отвечает «где лежит», эта
команда — «чего не написано».

``--json`` печатается через ``click.echo``, а не rich: перенос строки и
разметка молча портят значения (ADO-176).
"""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _make_session, _require_project_id, console
from .cmd_tree import tree

if TYPE_CHECKING:
    from cod_doc.config import Config

_SEVERITY_STYLE = {"major": "red", "minor": "yellow", "info": "dim"}


@tree.command("health")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--sync",
    "do_sync",
    is_flag=True,
    default=False,
    help="Записать пробелы в findings и закрыть вылеченные. Без флага — только показать.",
)
@click.option("--author", default="human:cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_health(
    ctx: click.Context,
    project: str,
    do_sync: bool,
    author: str,
    as_json: bool,
) -> None:
    """Показать пробелы в наполненности разделов; ``--sync`` пишет их в findings."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_node_health as svc

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf, commit=do_sync) as session:
        project_id = _require_project_id(session, project)
        seeded = svc.tree_is_seeded(session, project_id)
        issues = svc.assess(session, project_id, project_slug=project) if seeded else []
        rows = [
            {
                "code": i.code,
                "scope_kind": i.scope_kind,
                "scope_id": i.scope_id,
                "title": i.title,
                "body": i.body,
                "severity": i.severity,
            }
            for i in issues
        ]
        synced = (
            svc.sync(session, project_id=project_id, project_slug=project, author=author).as_dict()
            if do_sync
            else None
        )

    if as_json:
        payload: dict[str, object] = {"seeded": seeded, "count": len(rows), "issues": rows}
        if synced is not None:
            payload["synced"] = synced
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not seeded:
        console.print(
            "[dim]Дерево разделов не заведено — говорить о пробелах нечего. "
            f"Заведи: cod-doc doc tree init -p {project}[/dim]"
        )
        sys.exit(0)

    if not rows:
        console.print("[green]Пробелов не найдено: разделы наполнены.[/green]")
    else:
        table = Table(title=f"Пробелы в разделах — {project}", show_header=True)
        table.add_column("Важность", width=9)
        table.add_column("Правило", style="cyan", width=12)
        table.add_column("Раздел", width=14)
        table.add_column("Что не так")
        for row in rows:
            style = _SEVERITY_STYLE.get(str(row["severity"]), "")
            table.add_row(
                f"[{style}]{row['severity']}[/{style}]" if style else str(row["severity"]),
                str(row["code"]),
                str(row["scope_id"]),
                str(row["body"]),
            )
        console.print(table)

    if synced is not None:
        console.print(
            f"[dim]Находки: создано {synced['created']}, обновлено {synced['updated']}, "
            f"закрыто {synced['resolved']}, переоткрыто {synced['reopened']}[/dim]"
        )
    elif rows:
        console.print("[dim]Записать в findings: добавь --sync[/dim]")
