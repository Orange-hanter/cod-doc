"""CLI: `cod-doc rel` — граф связей между сущностями (RFC 39 §3.7).

Зеркало MCP-тулов `relate` / `unrelate` / `relations` / `relation_types` /
`relation_type_define` / `relation_type_deprecate` / `relation_verify`.
Ссылка — `kind:key` (`story:US-007`, `section:docs/a.md#intro`,
`code:path.py::func`). Импорты infra/services — в телах команд
(`tests/cli/test_cli_startup_is_light.py`).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import click

from cod_doc.cli._common import require_project_id_click

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker

_PROJECT = click.option("--project", "-p", required=True, help="Project slug")
_AUTHOR = click.option("--author", default="cli", show_default=True)
_JSON = click.option("--json", "as_json", is_flag=True, help="Machine-readable output")


def _factory(project: str, ctx: click.Context) -> sessionmaker[Session]:
    from cod_doc.infra.db import db_for_entry

    entry = ctx.obj["config"].get_project(project)
    if not entry:
        raise click.ClickException(f"Project not found: {project}")
    factory, _engine = db_for_entry(entry)
    return factory


@contextmanager
def _guard() -> Iterator[None]:
    """Ошибка связи — одно сообщение с подсказкой и код 1, без traceback."""
    from cod_doc.services.relation_service import RelationError

    try:
        yield
    except RelationError as exc:
        raise click.ClickException(str(exc)) from None


def _emit(payload: Any) -> None:  # noqa: ANN401 — любая JSON-сериализуемая структура
    """``--json`` идёт через click.echo: rich перенёс бы и разметил значения (ADO-176)."""
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@click.group()
def rel() -> None:
    """Граф связей: relate/unrelate между сущностями, словарь отношений."""


@rel.command("add")
@click.argument("from_ref")
@click.argument("type_", metavar="TYPE")
@click.argument("to_ref")
@_PROJECT
@click.option("--note", default=None)
@_AUTHOR
@_JSON
@click.pass_context
def rel_add(
    ctx: click.Context,
    from_ref: str,
    type_: str,
    to_ref: str,
    project: str,
    note: str | None,
    author: str,
    as_json: bool,
) -> None:
    """Связать FROM и TO отношением TYPE (имя, алиас или обратное имя)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import relation_service

    sf = _factory(project, ctx)
    with _guard(), transactional(sf) as session:
        project_id = require_project_id_click(session, project)
        edge = relation_service.relate(
            session,
            project_id=project_id,
            from_ref=from_ref,
            type=type_,
            to_ref=to_ref,
            note=note,
            author=author,
        )
        payload = relation_service.edge_to_dict(edge)
    if as_json:
        _emit(payload)
        return
    verb = "added" if edge.created else "exists"
    click.echo(f"{edge.from_ref} {edge.type} {edge.to_ref} ({verb})")


@rel.command("rm")
@click.argument("from_ref")
@click.argument("type_", metavar="TYPE")
@click.argument("to_ref")
@_PROJECT
@_AUTHOR
@_JSON
@click.pass_context
def rel_rm(
    ctx: click.Context,
    from_ref: str,
    type_: str,
    to_ref: str,
    project: str,
    author: str,
    as_json: bool,
) -> None:
    """Снять связь; код 0 и пометка, если её не было."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import relation_service

    sf = _factory(project, ctx)
    with _guard(), transactional(sf) as session:
        project_id = require_project_id_click(session, project)
        removed = relation_service.unrelate(
            session,
            project_id=project_id,
            from_ref=from_ref,
            type=type_,
            to_ref=to_ref,
            author=author,
        )
    if as_json:
        _emit({"removed": removed})
        return
    click.echo("removed" if removed else "no such relation; nothing removed")


@rel.command("ls")
@click.argument("ref")
@_PROJECT
@click.option("--in", "direction", flag_value="in", help="Только входящие")
@click.option("--out", "direction", flag_value="out", help="Только исходящие")
@click.option("--type", "-t", "types", multiple=True, help="Фильтр по типу (можно несколько)")
@_JSON
@click.pass_context
def rel_ls(
    ctx: click.Context,
    ref: str,
    project: str,
    direction: str | None,
    types: tuple[str, ...],
    as_json: bool,
) -> None:
    """Все связи сущности REF одной лентой."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import relation_service

    sf = _factory(project, ctx)
    with _guard(), transactional(sf) as session:
        project_id = require_project_id_click(session, project)
        views = relation_service.relations(
            session,
            project_id=project_id,
            ref=ref,
            direction=direction or "both",
            types=list(types) or None,
        )
        rows = [relation_service.view_to_dict(v) for v in views]
    if as_json:
        _emit(rows)
        return
    for r in rows:
        arrow = "→" if r["direction"] == "out" else "←"
        title = f"  {r['other_title']}" if r["other_title"] else ""
        broken = "" if r["resolved"] else "  [unresolved]"
        click.echo(f"{arrow} {r['label']:<16} {r['other']}  ({r['source']}){title}{broken}")


@rel.command("types")
@_PROJECT
@click.option("--from", "from_kind", default=None, help="Вид источника (story, task, …)")
@click.option("--to", "to_kind", default=None, help="Вид цели")
@_JSON
@click.pass_context
def rel_types(
    ctx: click.Context, project: str, from_kind: str | None, to_kind: str | None, as_json: bool
) -> None:
    """Словарь отношений: встроенные и проектные типы с числом рёбер."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import relation_service

    sf = _factory(project, ctx)
    with _guard(), transactional(sf) as session:
        project_id = require_project_id_click(session, project)
        infos = relation_service.relation_types(
            session, project_id=project_id, from_kind=from_kind, to_kind=to_kind
        )
        rows = [relation_service.type_info_to_dict(i) for i in infos]
    if as_json:
        _emit(rows)
        return
    for r in rows:
        flags = ",".join(k for k in ("symmetric", "acyclic", "allow_forward", "deprecated") if r[k])
        scope = f"{'|'.join(r['from_kinds'])} → {'|'.join(r['to_kinds'])}"
        click.echo(
            f"{r['name']} / {r['inverse']}  [{scope}]  edges={r['edges']}"
            + (f"  {flags}" if flags else "")
            + ("" if r["builtin"] else "  (project)")
        )


@rel.group("type")
def rel_type() -> None:
    """Проектные типы отношений: define, deprecate."""


@rel_type.command("define")
@click.argument("name")
@_PROJECT
@click.option("--inverse", default="", help="Имя со стороны цели (у --symmetric совпадает с NAME)")
@click.option("--from", "from_kinds", multiple=True, required=True, help="Вид источника")
@click.option("--to", "to_kinds", multiple=True, required=True, help="Вид цели")
@click.option("--symmetric", is_flag=True)
@click.option("--acyclic", is_flag=True)
@click.option("--allow-forward", is_flag=True, help="Цель может ещё не существовать")
@click.option("--description", default="")
@_AUTHOR
@_JSON
@click.pass_context
def rel_type_define(
    ctx: click.Context,
    name: str,
    project: str,
    inverse: str,
    from_kinds: tuple[str, ...],
    to_kinds: tuple[str, ...],
    symmetric: bool,
    acyclic: bool,
    allow_forward: bool,
    description: str,
    author: str,
    as_json: bool,
) -> None:
    """Завести проектный тип NAME без миграции."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import relation_service

    sf = _factory(project, ctx)
    with _guard(), transactional(sf) as session:
        project_id = require_project_id_click(session, project)
        created = relation_service.define_type(
            session,
            project_id=project_id,
            spec=relation_service.RelationTypeSpec(
                name=name,
                inverse=inverse,
                from_kinds=list(from_kinds),
                to_kinds=list(to_kinds),
                symmetric=symmetric,
                acyclic=acyclic,
                allow_forward=allow_forward,
                description=description,
            ),
            author=author,
        )
    payload = {
        "name": created.name,
        "inverse": created.inverse,
        "from_kinds": sorted(k.value for k in created.from_kinds),
        "to_kinds": sorted(k.value for k in created.to_kinds),
    }
    if as_json:
        _emit(payload)
        return
    click.echo(f"{created.name} / {created.inverse} defined")


@rel_type.command("deprecate")
@click.argument("name")
@_PROJECT
@_AUTHOR
@click.pass_context
def rel_type_deprecate(ctx: click.Context, name: str, project: str, author: str) -> None:
    """Запретить новые рёбра типа NAME; существующие остаются."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import relation_service

    sf = _factory(project, ctx)
    with _guard(), transactional(sf) as session:
        project_id = require_project_id_click(session, project)
        changed = relation_service.deprecate_type(
            session, project_id=project_id, name=name, author=author
        )
    click.echo(f"{name} deprecated" if changed else f"{name} already deprecated")


@rel.command("verify")
@_PROJECT
@click.option("--ref", "refs", multiple=True, help="Только рёбра этого источника (kind:key)")
@_JSON
@click.pass_context
def rel_verify(ctx: click.Context, project: str, refs: tuple[str, ...], as_json: bool) -> None:
    """Проверить code:/url: и ссылки вперёд; код 1, если есть битые."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import relation_service

    sf = _factory(project, ctx)
    with _guard(), transactional(sf) as session:
        project_id = require_project_id_click(session, project)
        report = relation_service.verify(session, project_id=project_id, refs=list(refs) or None)
        payload = relation_service.report_to_dict(report)
    if as_json:
        _emit(payload)
    else:
        click.echo(
            f"checked {report.checked}: resolved {report.resolved}, "
            f"filled {report.filled}, broken {len(report.broken)}"
        )
        for line in report.broken:
            click.echo(f"✗ {line}")
    if report.broken:
        raise click.exceptions.Exit(1)
