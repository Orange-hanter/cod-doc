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
    from cod_doc.services.drift_gate_service import GateReport

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
@click.option(
    "--changed-files",
    multiple=True,
    help="Сузить выборку до этих repo-относительных путей; повторяемый, допускает запятые",
)
@click.option(
    "--pr",
    type=int,
    default=None,
    help="Номер PR: без --changed-files берёт список файлов из него (`gh pr view`)",
)
@click.option(
    "--repo",
    default=None,
    help="OWNER/NAME для `gh` (по умолчанию — репозиторий по пути проекта)",
)
@click.option(
    "--comment",
    "post_comment",
    is_flag=True,
    default=False,
    help="Оставить/обновить комментарий гейта в --pr (идемпотентно, по маркеру)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="С --comment: показать тело комментария, ничего не отправляя",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def ctx_drift(
    ctx: click.Context,
    project: str,
    changed_files: tuple[str, ...],
    pr: int | None,
    repo: str | None,
    post_comment: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """DB↔markdown дрейф проекта (тот же shape, что и MCP ``ctx_drift``).

    С ``--changed-files`` / ``--pr`` превращается в drift-гейт PR (SYM-010):
    выборка сужается до затронутых файлов, к дрейфу добавляются нерезолвящиеся
    ссылки и frontmatter, а ``--comment`` кладёт находки в PR одним
    комментарием под маркером ``cod-doc:drift-gate:<project>``. Повторный
    прогон обновляет тот же комментарий.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import drift_gate_service, gh_service, projection_service

    cfg: Config = ctx.obj["config"]
    factory, engine, root = _project_session(cfg, project)

    if post_comment and pr is None:
        raise click.ClickException("--comment требует --pr N")

    files: list[str] | None = _normalise_path_patterns(changed_files) or None
    if files is None and pr is not None:
        try:
            files = gh_service.pr_changed_files(pr, repo=repo, cwd=root)
        except gh_service.GhError as exc:
            raise click.ClickException(str(exc)) from exc

    try:
        with transactional(factory, commit=False) as session:
            project_id = _require_project_id(session, project)
            report = projection_service.detect_project_drift(
                session, project_id, root_path=root, paths=files
            )
            gate = (
                drift_gate_service.collect(
                    session,
                    project=project,
                    project_id=project_id,
                    root_path=root,
                    changed_files=files,
                )
                if files is not None
                else None
            )
    finally:
        engine.dispose()

    comment_info: dict[str, Any] | None = None
    if gate is not None and post_comment and pr is not None:
        body = drift_gate_service.render_comment(gate, pr=pr)
        if dry_run:
            comment_info = {"action": "dry_run", "body": body}
        else:
            try:
                ref = gh_service.upsert_marker_comment(
                    pr,
                    body,
                    drift_gate_service.marker(project),
                    repo=repo,
                    cwd=root,
                )
            except gh_service.GhError as exc:
                raise click.ClickException(str(exc)) from exc
            comment_info = {
                "action": ref.action,
                "comment_id": ref.comment_id,
                "url": ref.url,
            }

    payload: dict[str, Any] = {
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
    if files is not None:
        payload["changed_files"] = files
    if gate is not None:
        payload["gate"] = gate.as_dict()
    if comment_info is not None:
        payload["comment"] = comment_info

    if as_json:
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
        return

    console.rule(f"[bold]Контекст: дрейф — {project}[/bold]")
    if files is not None:
        console.print(f"Файлов из PR/фильтра: [cyan]{len(files)}[/cyan]")
    console.print("  " + "  ".join(f"{status}: {count}" for status, count in report.counts.items()))
    console.print(f"Всего документов: {report.total_docs}")
    console.print(f"Проблем: [cyan]{report.problem_count}[/cyan]")

    if report.issues:
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
    else:
        console.print("[green]✅ Дрейф не обнаружен.[/green]")

    if gate is not None:
        _render_gate(gate, comment_info)


def _render_gate(gate: GateReport, comment_info: dict[str, Any] | None) -> None:
    """Человекочитаемая часть drift-гейта: находки + судьба комментария."""
    console.print(f"Находок гейта: [cyan]{gate.finding_count}[/cyan] ({gate.counts_by_rule})")
    if gate.findings:
        gate_table = Table(show_header=True, box=None, padding=(0, 1))
        gate_table.add_column("Правило", width=12)
        gate_table.add_column("Код", width=22)
        gate_table.add_column("Sev", width=8)
        gate_table.add_column("Файл", style="dim")
        gate_table.add_column("Находка")
        for finding in gate.findings:
            gate_table.add_row(
                finding.rule, finding.code, finding.severity, finding.path, finding.title
            )
        console.print(gate_table)
    if comment_info is None:
        return
    if comment_info["action"] == "dry_run":
        console.print("[yellow]dry-run — комментарий не отправлен:[/yellow]")
        console.print(comment_info["body"])
        return
    console.print(
        f"Комментарий [{comment_info['action']}]: "
        f"id={comment_info['comment_id']} {comment_info['url']}"
    )


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
