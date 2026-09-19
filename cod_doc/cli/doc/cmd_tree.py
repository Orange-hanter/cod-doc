"""`doc tree` — дерево документации: разделы, раскладка, Инбокс (ADO-116).

Зеркало MCP-семейства ``doc_tree_*`` / ``doc_node_*``. Мутация, живущая на
одной поверхности, — ровно то, что запрещает
``tests/services/test_doc_mutation_surface_parity.py``.

``--json`` печатается через ``click.echo``, а не rich: перенос строки и
разметка молча портят значения (ADO-176).
"""

from __future__ import annotations

import json as _json
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

    from cod_doc.config import Config


def _fail(message: str) -> None:
    """Печать ошибки и выход — как в остальных doc-командах."""
    console.print(f"[red]{message}[/red]")
    sys.exit(1)


def _guard() -> AbstractContextManager[None]:
    """Перевести типизированные ошибки сервиса в человеческое сообщение.

    Сервис бросает ``NodeNotFoundError`` / ``NodeAlreadyExistsError`` /
    ``NodeHasDocumentsError`` и ``ValidationError`` — их текст уже написан для
    человека. Без этой обёртки пользователь получал бы трейсбек (проверено на
    `doc tree node-rm --key audit`: 42 документа, отказ приходил стеком).
    """
    from cod_doc.services.doc_tree_service import (
        NodeAlreadyExistsError,
        NodeHasDocumentsError,
        NodeNotFoundError,
    )
    from cod_doc.services.validation import ValidationError

    @contextmanager
    def _cm() -> Iterator[None]:
        try:
            yield
        except ValidationError as exc:
            _fail(f"Validation error: {exc}")
        except NodeHasDocumentsError as exc:
            _fail(
                f"В разделе «{exc.node_key}» лежит документов: {exc.count}. "
                "Передай --reassign-to <ключ> или --force (документы уйдут в Инбокс)."
            )
        except NodeAlreadyExistsError as exc:
            _fail(f"Раздел «{exc.node_key}» уже существует.")
        except NodeNotFoundError as exc:
            _fail(f"Раздел «{exc.node_key}» не найден.")
        except LookupError as exc:
            _fail(str(exc))

    return _cm()


@doc.group("tree")
def tree() -> None:
    """Дерево документации: разделы, раскладка документов, Инбокс."""


@tree.command("show")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_show(ctx: click.Context, project: str, as_json: bool) -> None:
    """Показать разделы со счётчиками и число неразложенных документов."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_tree_service as svc

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        stats = svc.node_stats(session, project_id)
        unplaced = svc.unplaced_count(session, project_id)

    if as_json:
        click.echo(
            _json.dumps(
                {
                    "nodes": [
                        {
                            "node_key": s.node.node_key,
                            "title": s.node.title,
                            "intent": s.node.intent,
                            "position": s.node.position,
                            "min_docs": s.node.min_docs,
                            "is_inbox": s.node.is_inbox,
                            "doc_count": s.doc_count,
                            "under_filled": s.under_filled,
                        }
                        for s in stats
                    ],
                    "unplaced": unplaced,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not stats:
        console.print(
            f"[dim]Дерево не засеяно. Заведи разделы: cod-doc doc tree init -p {project}[/dim]"
        )
        return

    table = Table(title=f"Дерево документации — {project}", show_header=True)
    table.add_column("Раздел", style="cyan", no_wrap=True)
    table.add_column("Док.", justify="right", width=6)
    table.add_column("Название")
    for stat in stats:
        mark = "⚠️ " if stat.under_filled else ""
        table.add_row(
            stat.node.node_key,
            str(stat.doc_count),
            f"{mark}{stat.node.title}",
        )
    console.print(table)
    if unplaced:
        console.print(f"[yellow]Не разложено: {unplaced}[/yellow]")


@tree.command("unplaced")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_unplaced(ctx: click.Context, project: str, as_json: bool) -> None:
    """Документы, которые ещё не разложены по разделам."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_tree_service as svc

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        keys = svc.unplaced(session, project_id)

    if as_json:
        click.echo(
            _json.dumps({"count": len(keys), "doc_keys": keys}, indent=2, ensure_ascii=False)
        )
        return

    if not keys:
        console.print("[green]Инбокс пуст — весь корпус разложен.[/green]")
        return
    console.print(f"[yellow]Не разложено: {len(keys)}[/yellow]")
    for key in keys:
        console.print(f"  {key}")


@tree.command("init")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="human:cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_init(ctx: click.Context, project: str, author: str, as_json: bool) -> None:
    """Засеять дефолтное дерево разделов. Существующие ключи не трогает."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_tree_service as svc

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with _guard(), transactional(sf) as session:
        project_id = _require_project_id(session, project)
        created = svc.init_tree(session, project_id=project_id, author=author)
        keys = [n.node_key for n in created]

    if as_json:
        click.echo(
            _json.dumps({"created": keys, "created_count": len(keys)}, indent=2, ensure_ascii=False)
        )
        return

    if not keys:
        console.print("[dim]Дерево уже засеяно — ничего не создано.[/dim]")
        return
    console.print(f"[green]Создано разделов: {len(keys)}[/green]")
    for key in keys:
        console.print(f"  {key}")


@tree.command("classify")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    default=False,
    help="Записать раскладку в БД. Без флага — только показать.",
)
@click.option(
    "--all",
    "include_placed",
    is_flag=True,
    default=False,
    help="Переразложить и то, что уже лежит в разделах (перебьёт ручную раскладку).",
)
@click.option("--author", default="human:cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_classify(
    ctx: click.Context,
    project: str,
    apply_changes: bool,
    include_placed: bool,
    author: str,
    as_json: bool,
) -> None:
    """Разложить документы по разделам детерминированными правилами.

    По умолчанию — сухой прогон по неразложенным документам. Ручную раскладку
    команда не трогает, пока не передан ``--all``.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_tree_service as svc

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    dry_run = not apply_changes

    with _guard(), transactional(sf, commit=apply_changes) as session:
        project_id = _require_project_id(session, project)
        report = svc.classify_project(
            session,
            project_id=project_id,
            author=author,
            dry_run=dry_run,
            only_unplaced=not include_placed,
        )
        by_node = report.by_node
        placed = len(report.placed)
        leftovers = [(p.doc_key, p.reason) for p in report.unplaced]

    if as_json:
        click.echo(
            _json.dumps(
                {
                    "dry_run": dry_run,
                    "placed": placed,
                    "unplaced": len(leftovers),
                    "by_node": by_node,
                    "unplaced_docs": [{"doc_key": k, "reason": r} for k, r in leftovers],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    head = "Сухой прогон" if dry_run else "Применено"
    console.print(f"[bold]{head}:[/bold] размещено {placed}, в Инбоксе {len(leftovers)}")
    if by_node:
        table = Table(show_header=True)
        table.add_column("Раздел", style="cyan")
        table.add_column("Док.", justify="right", width=6)
        for key in sorted(by_node, key=lambda k: -by_node[k]):
            table.add_row(key, str(by_node[key]))
        console.print(table)
    if leftovers:
        console.print("[yellow]Инбокс:[/yellow]")
        for key, reason in leftovers:
            console.print(f"  {key} [dim]— {reason}[/dim]")
    if dry_run and placed:
        console.print("[dim]Записать: добавь --apply[/dim]")


@tree.command("move")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--doc-key", required=True, help="Документ, который перекладываем")
@click.option(
    "--node",
    "node_key",
    default=None,
    help="Целевой раздел. Не указан — документ возвращается в Инбокс.",
)
@click.option("--author", default="human:cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_move(
    ctx: click.Context,
    project: str,
    doc_key: str,
    node_key: str | None,
    author: str,
    as_json: bool,
) -> None:
    """Положить документ в раздел; без ``--node`` — вернуть в Инбокс."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_tree_service as svc

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with _guard(), transactional(sf) as session:
        project_id = _require_project_id(session, project)
        node = svc.assign(
            session,
            project_id=project_id,
            doc_key=doc_key,
            node_key=node_key,
            author=author,
        )
        landed = node.node_key if node is not None else None

    if as_json:
        click.echo(
            _json.dumps({"doc_key": doc_key, "node_key": landed}, indent=2, ensure_ascii=False)
        )
        return
    console.print(f"[green]{doc_key}[/green] → {landed or 'Инбокс'}")


@tree.command("node-add")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--key", "node_key", required=True, help="Ключ раздела, например data-model")
@click.option("--title", required=True, help="Человеческое название")
@click.option("--intent", default="", help="Что должно лежать в разделе")
@click.option("--parent", "parent_key", default=None, help="Родительский раздел")
@click.option("--position", type=int, default=None, help="Порядок показа; по умолчанию в конец")
@click.option("--min-docs", type=int, default=0, show_default=True)
@click.option("--author", default="human:cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_node_add(
    ctx: click.Context,
    project: str,
    node_key: str,
    title: str,
    intent: str,
    parent_key: str | None,
    position: int | None,
    min_docs: int,
    author: str,
    as_json: bool,
) -> None:
    """Завести раздел."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_tree_service as svc

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with _guard(), transactional(sf) as session:
        project_id = _require_project_id(session, project)
        node = svc.create_node(
            session,
            project_id=project_id,
            node_key=node_key,
            title=title,
            intent=intent,
            parent_key=parent_key,
            position=position,
            min_docs=min_docs,
            author=author,
        )
        out = {"node_key": node.node_key, "title": node.title, "position": node.position}

    if as_json:
        click.echo(_json.dumps(out, indent=2, ensure_ascii=False))
        return
    console.print(f"[green]Раздел создан:[/green] {out['node_key']} — {out['title']}")


@tree.command("node-edit")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--key", "node_key", required=True)
@click.option("--title", default=None)
@click.option("--intent", default=None)
@click.option("--position", type=int, default=None)
@click.option("--min-docs", type=int, default=None)
@click.option("--author", default="human:cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_node_edit(
    ctx: click.Context,
    project: str,
    node_key: str,
    title: str | None,
    intent: str | None,
    position: int | None,
    min_docs: int | None,
    author: str,
    as_json: bool,
) -> None:
    """Поправить раздел. Не переданные поля остаются как есть."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_tree_service as svc

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with _guard(), transactional(sf) as session:
        project_id = _require_project_id(session, project)
        node = svc.update_node(
            session,
            project_id=project_id,
            node_key=node_key,
            title=title,
            intent=intent,
            position=position,
            min_docs=min_docs,
            author=author,
        )
        out = {"node_key": node.node_key, "title": node.title, "position": node.position}

    if as_json:
        click.echo(_json.dumps(out, indent=2, ensure_ascii=False))
        return
    console.print(f"[green]Раздел обновлён:[/green] {out['node_key']} — {out['title']}")


@tree.command("node-rm")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--key", "node_key", required=True)
@click.option("--reassign-to", default=None, help="Куда перенести документы раздела")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Удалить непустой раздел, вернув его документы в Инбокс.",
)
@click.option("--author", default="human:cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_node_rm(
    ctx: click.Context,
    project: str,
    node_key: str,
    reassign_to: str | None,
    force: bool,
    author: str,
    as_json: bool,
) -> None:
    """Удалить раздел.

    Непустой раздел не удаляется молча: либо ``--reassign-to``, либо
    ``--force``, который вернёт его документы в Инбокс.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_tree_service as svc

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with _guard(), transactional(sf) as session:
        project_id = _require_project_id(session, project)
        moved = svc.delete_node(
            session,
            project_id=project_id,
            node_key=node_key,
            reassign_to=reassign_to,
            force=force,
            author=author,
        )

    if as_json:
        click.echo(
            _json.dumps(
                {"node_key": node_key, "moved": moved, "reassign_to": reassign_to},
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    console.print(f"[green]Раздел удалён:[/green] {node_key} (перенесено документов: {moved})")
