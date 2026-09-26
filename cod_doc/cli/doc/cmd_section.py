"""`doc section` — print section bodies by anchor (AFT-009)."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("section")
@click.argument("doc_key")
@click.argument("anchors", nargs=-1, required=True)
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--json", "as_json", is_flag=True, help="Вывод в JSON (как у MCP doc_section_get)")
@click.pass_context
def doc_section(
    ctx: click.Context, doc_key: str, anchors: tuple[str, ...], project: str, as_json: bool
) -> None:
    """Тела секций документа по якорям — без чтения тела всего документа.

    Несколько якорей — один вызов, вывод в порядке запроса. ``head_revision_id``
    каждой секции — токен ``--expected-revision`` для ``doc patch`` /
    ``expected_parent_revision_id`` для MCP ``doc_patch_section``.
    Неизвестный якорь — miss со списком доступных якорей и код возврата 1.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        d = doc_service.get(session, project_id, doc_key)
        if d is None or d.row_id is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)
        result = doc_service.read_sections(session, d.row_id, anchors)

    has_miss = any(item.get("found") is False for item in result)
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False))
        sys.exit(1 if has_miss else 0)

    for item in result:
        if item.get("found") is False:
            available = ", ".join(item["available_anchors"])
            console.print(
                f"якорь {item['anchor']} не найден; доступные: {available}",
                style="red",
                markup=False,
                highlight=False,
            )
            continue
        console.print(
            f"## {item['anchor']} (head_revision_id: {item['head_revision_id']})",
            style="bold",
            markup=False,
            highlight=False,
        )
        console.print(item["body"], markup=False, highlight=False)
    sys.exit(1 if has_miss else 0)
