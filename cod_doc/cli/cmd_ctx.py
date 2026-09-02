"""RFC 22 §3.3/3.4 (SYM-008 / ADO-057): `cod-doc ctx docs|drift|search`.

Три read-only подкоманды, которые отдают контекст проекта для внешних
потребителей (ZAIrgRush, Orakul) поверх тех же сервисов, что и MCP-алиасы
`ctx_docs` / `ctx_drift`. Все три работают в ``commit=False``-транзакции,
т.е. гарантированно ничего не пишут в БД.
"""

from __future__ import annotations

import fnmatch
import json as _json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config
    from cod_doc.domain.entities import Document

console = Console()
log = get_logger("cli.ctx")


def _project_session(cfg: Config, project: str) -> tuple[sessionmaker[Session], Engine, Path]:
    """Open (session_factory, engine, root_path) for a registered project.

    Raises ``click.ClickException`` if the project is unknown.
    """
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project)
    if not entry:
        raise click.ClickException(f"Проект не найден: {project}")
    factory, engine = db_for_entry(entry)
    root = Path(entry.path).expanduser().resolve()
    return factory, engine, root


def _require_project_id(session: Session, project: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project)
    if proj is None or proj.row_id is None:
        raise click.ClickException(
            f"Проект '{project}' не зарегистрирован в БД. Сначала: cod-doc project add"
        )
    return proj.row_id


def _normalise_path_patterns(raw: tuple[str, ...]) -> list[str]:
    """Развернуть повторяемые --paths и внутренние запятые в единый список."""
    out: list[str] = []
    for item in raw:
        for part in item.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _doc_matches_paths(doc: Document, patterns: list[str]) -> bool:
    """True if doc.path or doc.doc_key matches any glob pattern."""
    if not patterns:
        return True
    for pat in patterns:
        if fnmatch.fnmatchcase(doc.path, pat) or fnmatch.fnmatchcase(doc.doc_key, pat):
            return True
    return False


def _doc_to_compact(doc: Document) -> dict[str, Any]:
    """Минимальный стабильный словарь документа (компактнее ``doc_to_dict``)."""
    return {
        "doc_key": doc.doc_key,
        "path": doc.path,
        "title": doc.title,
        "type": doc.type.value,
        "status": doc.status.value,
        "projection_hash": doc.projection_hash,
        "last_updated": doc.last_updated.isoformat() if doc.last_updated else None,
    }


def _estimate_tokens(body: str | None) -> int:
    """Наивная эвристика: ~4 байта UTF-8 на токен для английского/кода.

    Не претендует на точность LLM-токенайзера; даёт стабильную верхнюю оценку
    для бюджетирования контекстного окна.
    """
    if not body:
        return 0
    return len(body.encode("utf-8")) // 4


def _collect_links_at_risk(
    session: Session, project_id: int, docs: list[Document]
) -> list[dict[str, Any]]:
    """Сломанные ссылки из переданных документов.

    Работает внутри ``commit=False``-сессии: ``resolve_section`` обновляет
    derived-таблицу ``link`` только в памяти, итоговый ``rollback`` сбрасывает
    любые изменения. Это позволяет честно считать ссылки ``broken`` без записи.
    """
    from cod_doc.services import doc_service, link_service

    at_risk: list[dict[str, Any]] = []
    for doc in docs:
        if doc.row_id is None:
            continue
        sections = doc_service.get_sections(session, doc.row_id)
        for section in sections:
            if section.row_id is None:
                continue
            # resolve_section синхронизирует и разрешает ссылки секции.
            link_service.resolve_section(session, section.row_id)
            at_risk.extend(
                {
                    "raw": link.raw,
                    "kind": link.kind.value,
                    "broken_reason": link.broken_reason,
                    "source_doc_key": doc.doc_key,
                    "source_section_anchor": section.anchor,
                }
                for link in link_service.list_for_section(session, section.row_id)
                if not link.resolved
            )
    return at_risk


@click.group()
def ctx() -> None:
    """Контекст проекта для внешних потребителей (docs / drift / search)."""


@ctx.command("docs")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option(
    "--paths",
    multiple=True,
    help="Glob-паттерн для фильтрации по path или doc_key; повторяемый",
)
@click.option(
    "--budget-tokens",
    type=int,
    default=None,
    help="Ограничить выборку документами, укладывающимися в бюджет токенов",
)
@click.option(
    "--include-body",
    is_flag=True,
    default=False,
    help="Включить отрендеренное тело документа в JSON (для prompt-контекста)",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def ctx_docs(
    ctx: click.Context,
    project: str,
    paths: tuple[str, ...],
    budget_tokens: int | None,
    include_body: bool,
    as_json: bool,
) -> None:
    """Список документов проекта с оценкой токенов и рискованными ссылками."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service

    cfg: Config = ctx.obj["config"]
    factory, engine, _root = _project_session(cfg, project)
    patterns = _normalise_path_patterns(paths)

    try:
        # commit=False гарантирует, что resolve_section не оставит изменений.
        with transactional(factory, commit=False) as session:
            project_id = _require_project_id(session, project)
            all_docs = doc_service.list_for_project(session, project_id)
            filtered = [d for d in all_docs if _doc_matches_paths(d, patterns)]

            # Применяем бюджет: идём в порядке doc_key, пока сумма токенов
            # не превысит заданный лимит.
            selected: list[Any] = []
            bodies: dict[str, str] = {}
            token_estimate = 0
            for doc in sorted(filtered, key=lambda d: d.doc_key):
                body = (
                    doc_service.render_body(session, doc.row_id) if doc.row_id is not None else None
                )
                doc_tokens = _estimate_tokens(body)
                if budget_tokens is not None and token_estimate + doc_tokens > budget_tokens:
                    continue
                selected.append(doc)
                token_estimate += doc_tokens
                if include_body and body:
                    bodies[doc.doc_key] = body

            links_at_risk = _collect_links_at_risk(session, project_id, selected)
            docs_out = [_doc_to_compact(d) for d in selected]
            for d in docs_out:
                if d["doc_key"] in bodies:
                    d["body"] = bodies[d["doc_key"]]
            payload = {
                "docs": docs_out,
                "links_at_risk": links_at_risk,
                "token_estimate": token_estimate,
            }
    finally:
        engine.dispose()

    if as_json:
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
        return

    console.rule(f"[bold]Контекст: документы — {project}[/bold]")
    console.print(f"Документов: [cyan]{len(docs_out)}[/cyan]")
    console.print(f"Оценка токенов: [cyan]{token_estimate}[/cyan]")
    console.print(f"Ссылок под риском: [cyan]{len(links_at_risk)}[/cyan]")
    if docs_out:
        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column("Doc key", style="cyan", no_wrap=True)
        table.add_column("Тип")
        table.add_column("Статус")
        table.add_column("Заголовок")
        for d in docs_out:
            table.add_row(d["doc_key"], d["type"], d["status"], d["title"])
        console.print(table)
    if links_at_risk:
        console.print("[yellow]Ссылки под риском:[/yellow]")
        for link in links_at_risk:
            console.print(
                f"  • [{link['source_doc_key']}#{link['source_section_anchor']}] "
                f"{link['raw']} — {link['broken_reason']}"
            )


@ctx.command("drift")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def ctx_drift(ctx: click.Context, project: str, as_json: bool) -> None:
    """DB↔markdown дрейф проекта (тот же shape, что и MCP ``ctx_drift``)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import projection_service

    cfg: Config = ctx.obj["config"]
    factory, engine, root = _project_session(cfg, project)

    try:
        with transactional(factory, commit=False) as session:
            project_id = _require_project_id(session, project)
            report = projection_service.detect_project_drift(session, project_id, root_path=root)
    finally:
        engine.dispose()

    payload = {
        "project": project,
        "total_docs": report.total_docs,
        "problem_count": report.problem_count,
        "counts": report.counts,
        "issues": [
            {
                "doc_key": item.doc_key,
                "path": item.path,
                "status": item.report.status.value,
                "projection_hash": item.report.projection_hash,
                "db_content_hash": item.report.db_content_hash,
                "file_hash": item.report.file_hash,
            }
            for item in report.issues
        ],
    }

    if as_json:
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
        return

    console.rule(f"[bold]Контекст: дрейф — {project}[/bold]")
    console.print("  " + "  ".join(f"{status}: {count}" for status, count in report.counts.items()))
    console.print(f"Всего документов: {report.total_docs}")
    console.print(f"Проблем: [cyan]{report.problem_count}[/cyan]")
    if not report.issues:
        console.print("[green]✅ Дрейф не обнаружен.[/green]")
        return

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Статус", width=18)
    table.add_column("Doc key", style="cyan")
    table.add_column("Путь", style="dim")
    for item in report.issues:
        table.add_row(
            item.report.status.value,
            item.doc_key,
            item.path,
        )
    console.print(table)


@ctx.command("search")
@click.argument("query")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def ctx_search(ctx: click.Context, query: str, project: str, as_json: bool) -> None:
    """Поиск по проекту (FTS5) — тот же движок, что и ``cod-doc search``."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import search_service

    cfg: Config = ctx.obj["config"]
    factory, engine, _root = _project_session(cfg, project)

    try:
        with transactional(factory, commit=False) as session:
            project_id = _require_project_id(session, project)
            result = search_service.search(session, project_id=project_id, query=query)
    finally:
        engine.dispose()

    if as_json:
        print(_json.dumps(result, ensure_ascii=False, indent=2))
        return

    console.rule(f"[bold]Контекст: поиск — {project}[/bold]")
    if result["total"] == 0:
        console.print(f"[dim]Ничего не найдено по запросу {query!r}.[/dim]")
        return

    console.print(f"Найдено: [cyan]{result['total']}[/cyan]")
    for kind, hits in result["by_kind"].items():
        if not hits:
            continue
        table = Table(title=f"{kind.upper()} ({len(hits)})", show_header=True)
        table.add_column("Ref", style="cyan", no_wrap=True)
        table.add_column("Title")
        table.add_column("Snippet")
        for h in hits:
            snippet = h["snippet"].replace("<mark>", "[bold yellow]").replace("</mark>", "[/]")
            table.add_row(h["ref"], h["title"], snippet)
        console.print(table)


@ctx.command("structure")
@click.option("--project", "-p", required=True)
@click.option("--head-sha", default=None)
@click.option("--fingerprint", "snapshot_fingerprint", default=None)
@click.option("--scope", "scope_refs", multiple=True)
@click.option("--budget-tokens", default=4000, type=int)
@click.option("--depth", "dependency_depth", default=1, type=int)
@click.pass_context
def ctx_structure(
    ctx: click.Context,
    project: str,
    head_sha: str | None,
    snapshot_fingerprint: str | None,
    scope_refs: tuple[str, ...],
    budget_tokens: int,
    dependency_depth: int,
) -> None:
    """Pinned BFS structure slice. Requires head SHA or snapshot fingerprint."""
    from cod_doc.infra.db import transactional
    from cod_doc.services.structure_context import build_structure_context
    from cod_doc.services.structure_protocol import StructureProtocolError

    cfg: Config = ctx.obj["config"]
    factory, engine, _root = _project_session(cfg, project)
    try:
        with transactional(factory, commit=False) as session:
            project_id = _require_project_id(session, project)
            payload = build_structure_context(
                session,
                project_id,
                project_slug=project,
                head_sha=head_sha,
                snapshot_fingerprint=snapshot_fingerprint,
                scope_refs=list(scope_refs),
                dependency_depth=dependency_depth,
                budget_tokens=budget_tokens,
            )
    except StructureProtocolError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        engine.dispose()
    print(_json.dumps(payload, ensure_ascii=False, indent=2, default=str))
