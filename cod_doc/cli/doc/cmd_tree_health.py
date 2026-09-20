"""`doc tree health` — пробелы в наполненности разделов.

Зеркало MCP-семейства ``doc_node_health_*``. Дерево отвечает «где лежит», эта
команда — «чего не написано».

``--json`` печатается через ``click.echo``, а не rich: перенос строки и
разметка молча портят значения (ADO-176).
"""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING, Any

import click
from rich.table import Table

from ._common import _make_session, _require_project_id, console
from .cmd_tree import tree

if TYPE_CHECKING:
    from cod_doc.config import Config

_SEVERITY_STYLE = {"major": "red", "minor": "yellow", "info": "dim"}


def _fail(message: str) -> None:
    """Печать ошибки и выход — как в остальных командах группы ``doc``."""
    console.print(f"[red]{message}[/red]")
    sys.exit(1)


def _print_issues(project: str, rows: list[dict[str, Any]]) -> None:
    """Таблица пробелов; пустой список — не ошибка, а хорошая новость."""
    if not rows:
        console.print("[green]Пробелов не найдено: разделы наполнены.[/green]")
        return
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


def _print_counters(
    rows: list[dict[str, Any]],
    synced: dict[str, Any] | None,
    analyzed: dict[str, Any] | None,
) -> None:
    """Что именно записалось: без этого ``--sync`` выглядит как no-op."""
    if synced is not None:
        console.print(
            f"[dim]Находки: создано {synced['created']}, обновлено {synced['updated']}, "
            f"закрыто {synced['resolved']}, переоткрыто {synced['reopened']}[/dim]"
        )
    elif rows:
        console.print("[dim]Записать в findings: добавь --sync[/dim]")

    if analyzed is not None:
        # `не подтверждено` печатается всегда: без него прогон, на котором
        # вердикт промахнулся впервые, выглядит как полный no-op, и гистерезис
        # читается как поломка.
        console.print(
            f"[dim]Вердикт модели: разделов {analyzed['sections']}, "
            f"не покрывают назначение {analyzed['issues']}, "
            f"закрыто {analyzed['resolved']}, "
            f"не подтверждено {analyzed['missed']}[/dim]"
        )
        # Неполный ответ — не ошибка прогона, но и не норма: находки таких
        # разделов не сверялись вовсе. Молчать об этом значит прятать
        # деградацию модели за словом «закрыто 0».
        if analyzed["unanswered"]:
            console.print(
                f"[yellow]Модель промолчала о {analyzed['unanswered']} раздел(ах) — "
                f"их находки не сверялись ({analyzed['skipped']} пропущено).[/yellow]"
            )


@tree.command("health")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--sync",
    "do_sync",
    is_flag=True,
    default=False,
    help="Записать пробелы в findings и закрыть вылеченные. Без флага — только показать.",
)
@click.option(
    "--analyze",
    "do_analyze",
    is_flag=True,
    default=False,
    help="Спросить модель, покрывают ли документы раздела его intent. Ходит в сеть.",
)
@click.option("--author", default="human:cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def tree_health(
    ctx: click.Context,
    project: str,
    do_sync: bool,
    do_analyze: bool,
    author: str,
    as_json: bool,
) -> None:
    """Показать пробелы в наполненности разделов; ``--sync`` пишет их в findings."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_node_health as svc
    from cod_doc.services import doc_node_intent
    from cod_doc.services.ai_text import AIBackendError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    writes = do_sync or do_analyze
    llm_error = ""

    with transactional(sf, commit=writes) as session:
        project_id = _require_project_id(session, project)
        seeded = svc.tree_is_seeded(session, project_id)
        issues = svc.assess(session, project_id, project_slug=project) if seeded else []
        rows: list[dict[str, Any]] = [
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
        # Ошибку модели глотать нельзя: пустой список вердиктов закрыл бы
        # находки LLM-партиции как «вылеченные». Но до пользователя она должна
        # дойти строкой, а не трейсбеком, и выход — уже за границей транзакции:
        # `sys.exit` отсюда откатил бы записанную детерминированную часть,
        # хотя с ней всё в порядке.
        analyzed = None
        if do_analyze:
            try:
                analyzed = doc_node_intent.analyze(
                    session, project_id=project_id, cfg=cfg, author=author
                )
            except AIBackendError as exc:
                llm_error = f"Модель недоступна: {exc}"
            except _json.JSONDecodeError as exc:
                llm_error = (
                    f"Модель вернула не JSON ({exc}). Находки модели не тронуты. "
                    "Обычно это обрыв ответа: проверь лимит токенов у провайдера."
                )

    if as_json:
        payload: dict[str, object] = {"seeded": seeded, "count": len(rows), "issues": rows}
        if synced is not None:
            payload["synced"] = synced
        if analyzed is not None:
            payload["analyzed"] = analyzed
        if llm_error:
            payload["llm_error"] = llm_error
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        if llm_error:
            sys.exit(1)
        return

    if not seeded:
        console.print(
            "[dim]Дерево разделов не заведено — говорить о пробелах нечего. "
            f"Заведи: cod-doc doc tree init -p {project}[/dim]"
        )
        sys.exit(0)

    _print_issues(project, rows)
    _print_counters(rows, synced, analyzed)
    if llm_error:
        _fail(llm_error)
