"""`cod-doc flow` — метрики потока (RFC 33 §3.1).

``rebuild-metrics`` — зеркало MCP-тула ``flow_metrics_rebuild`` (FLW-003):
пересчёт ``task_metrics`` закрытых задач из журнала переходов. Импорты
``infra``/``services`` — в теле команды (ADO-179); ``--json`` — через
``click.echo`` (ADO-176).
"""

from __future__ import annotations

import dataclasses
import json as _json
from typing import TYPE_CHECKING

import click
from rich.console import Console

if TYPE_CHECKING:
    from cod_doc.config import Config

console = Console()


@click.group()
def flow() -> None:
    """Метрики потока задач: время в работе и в блокировке."""


@flow.command("rebuild-metrics")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--author", default="cli", show_default=True, help="Автор события")
@click.option("--dry-run", is_flag=True, default=False, help="Только отчёт, без записи")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def rebuild_metrics(
    ctx: click.Context, project: str, author: str, dry_run: bool, as_json: bool
) -> None:
    """Пересчитать task_metrics закрытых задач из журнала переходов (FLW-003).

    Время в in_progress и blocked — по событиям task.checked_out /
    task.status_changed / task.completed. Идемпотентно; недостающие строки
    заводятся. Задачи старше журнала событий остаются без длительностей.
    """
    from cod_doc.cli._common import require_project_id_click
    from cod_doc.infra.db import db_for_entry, transactional
    from cod_doc.services import metrics_service

    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(project)
    if entry is None:
        raise click.ClickException(f"Project not found: {project}")
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory, commit=not dry_run) as session:
            report = metrics_service.rebuild(
                session,
                project_id=require_project_id_click(session, project),
                author=author,
                dry_run=dry_run,
            )
    finally:
        engine.dispose()

    payload = {**dataclasses.asdict(report), "dry_run": dry_run}
    if as_json:
        click.echo(_json.dumps(payload, ensure_ascii=False))
        return
    prefix = "🔍 Dry run — " if dry_run else "✅ "
    console.print(
        f"{prefix}задач: {report.tasks}, заведено строк: {report.created}, "
        f"изменено: {report.updated}, с in_progress_hours: {report.with_in_progress}, "
        f"без событий: {report.without_events}",
        markup=False,
    )
