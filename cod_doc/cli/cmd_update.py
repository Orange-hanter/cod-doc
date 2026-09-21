"""ADO-192: ``cod-doc update`` (алиас ``upgrade``) — самообновление с автопочинкой.

Поверхность над :mod:`cod_doc.services.update_service`: четыре фазы одной
командой — свап рантайма, миграции новым бинарём, рестарт демонов, починка
состояния проектов. Механика фаз и subprocess-relay описаны в докстринге
сервиса; здесь — флаги, подтверждение и печать.

Три вещи, которые видно только отсюда:

**Одно подтверждение на весь прогон.** План печатается таблицей с колонкой
«Обратимо» — она и есть предмет вопроса: свап рантайма откатывается, миграции
и ``doc import`` нет. Цепочка вопросов отвергнута: «нет» на середине оставило
бы машину в промежуточном состоянии, а сцепить такое в скрипт нельзя. Под
``--resume-json`` вопроса нет вовсе — человек уже ответил родителю.

**Неинтерактив не молчит.** Не-TTY плюс необратимое действие без ``--yes`` —
это выход 2 с указанием и ``--yes``, и ``--dry-run``. Молча продолжить нельзя,
зависнуть на ``click.confirm`` под launchd — тем более.

**stdout принадлежит машине.** Под ``--json`` туда уходит ровно один объект,
весь прогресс и все таблицы — в stderr. В resume-режиме stdout принадлежит
родителю (дочерний процесс наследует его дескрипторы), поэтому человекочитаемый
отчёт ребёнка тоже идёт в stderr: иначе относительно родительского ``--json``
в потоке оказалось бы два объекта.

Импорты ``cod_doc.services.*`` — только в телах функций (ADO-179):
``update_service`` тянет SQLAlchemy и Alembic, и на уровне модуля он уронил бы
``tests/cli/test_cli_startup_is_light.py`` вместе со временем старта любой
другой команды.
"""

from __future__ import annotations

import json as _json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table

from cod_doc.cli.cmd_runtime import (
    PYTHON_ENV,
    SHA_SHOWN,
    agents_dir,
    logs_dir,
    render_services,
    runtime_dir,
    short_version,
    workdir,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cod_doc.config import Config
    from cod_doc.services.launchd_service import ServiceStatus
    from cod_doc.services.project_service import MigrateResult
    from cod_doc.services.repair_service import RepairResult
    from cod_doc.services.update_service import InstallReport, SkipFlags, UpdatePlan, UpdateReport

console = Console()
err_console = Console(stderr=True)

#: Заголовки строк плана. Буква фазы — та же, что в докстринге сервиса.
_PHASE_A = "A · рантайм"
_PHASE_B = "B · миграции"
_PHASE_C = "C · сервисы"
_PHASE_D = "D · починка"

#: Колонка «Обратимо». Пустое место здесь заставляло бы человека гадать.
_A_REVERSIBLE = "свап — да (runtime rollback); установка в PATH — нет"
_B_REVERSIBLE = "нет — alembic downgrade через N ревизий не делаем"
_C_REVERSIBLE = "да — kickstart повторим"
_D_REVERSIBLE = "doc import и снятие замков — нет"
_NOT_APPLICABLE = "—"

#: Почему спрашивать некого и что с этим делать. Оба флага названы намеренно:
#: ``--yes`` для того, кто знает, ``--dry-run`` для того, кто хочет посмотреть.
_NEEDS_CONSENT = (
    "в плане есть необратимые действия, а подтвердить их некому: "
    "stdin не терминал. Запусти с --yes, если согласен заранее, "
    "или посмотри план через --dry-run."
)
_JSON_NEEDS_CONSENT = (
    "--json подразумевает неинтерактивный запуск, а спросить о необратимых "
    "действиях будет некого: добавь --yes или --dry-run."
)

#: Предупреждение в плане. Отдельно от ``DB_NOT_ROLLED_BACK`` (тот про
#: состоявшийся откат): здесь речь о том, чего откат НЕ вернёт, если он
#: понадобится, и время у фразы другое.
_ROLLBACK_KEEPS_DB = (
    "Откат рантайма (cod-doc runtime rollback) вернёт прежний код, но НЕ откатит БД: "
    "накатанные миграции останутся, и прежний код может не знать новой схемы."
)

#: Вопрос ровно один — см. докстринг модуля.
_CONFIRM = "Продолжить?"

#: Что печатаем вместо длительности у фазы, которой не было.
_NO_DURATION = "—"

#: Строки прогресса. Собираются по плану, а не по намерению: заголовок фазы,
#: которой не будет, отправляет человека искать в отчёте следы работы, где
#: стоит «пропущена».
_AHEAD_INSTALL = "фаза A: сборка и свап рантайма"
_AHEAD_RELAY = "фазы B–D исполнит новый бинарь"
_AHEAD_MIGRATE = "фаза B: миграции"
_AHEAD_SERVICES = "фаза C: рестарт сервисов"
_AHEAD_REPAIR = "фаза D: починка проектов"
_PLAN_REF = "разрешение ревизии"
_PLAN_DIAGNOSE = "диагноз починки"


@dataclass(slots=True)
class _Options:
    """Разобранные флаги команды — чтобы не таскать их дюжиной аргументов."""

    ref: str | None
    repo: Path | None
    projects: list[str]
    ttl_minutes: int
    skip: SkipFlags
    install_services: bool
    as_json: bool
    yes: bool
    dry_run: bool


# ── план ────────────────────────────────────────────────────────────────────


def _skipped(flag: str) -> str:
    return f"пропущено ({flag})"


def _row_install(plan: UpdatePlan) -> tuple[str, str, str, str]:
    if plan.skip.install:
        return (_PHASE_A, _skipped("--skip-install"), _NOT_APPLICABLE, _NOT_APPLICABLE)
    sha = (plan.target_sha or "?")[:SHA_SHOWN]
    return (
        _PHASE_A,
        f"сборка {plan.ref} и свап {plan.runtime}",
        f"{plan.current_version or '?'} → {sha}",
        _A_REVERSIBLE,
    )


def _row_migrate(plan: UpdatePlan) -> tuple[str, str, str, str]:
    if plan.skip.migrate:
        return (_PHASE_B, _skipped("--skip-migrate"), _NOT_APPLICABLE, _NOT_APPLICABLE)
    scope = f"проектов: {len(plan.projects)}"
    if plan.hub_projects:
        scope += f", из них на общей hub-БД: {len(plan.hub_projects)}"
    return (_PHASE_B, "alembic upgrade head", scope, _B_REVERSIBLE)


def _row_services(plan: UpdatePlan, *, service_count: int) -> tuple[str, str, str, str]:
    if plan.skip.install:
        return (
            _PHASE_C,
            "пропущено: рантайм не менялся — перезапускать нечего",
            _NOT_APPLICABLE,
            _NOT_APPLICABLE,
        )
    return (_PHASE_C, "launchctl kickstart -k", f"лейблов: {service_count}", _C_REVERSIBLE)


def _counters(counters: Sequence[dict[str, int]]) -> dict[str, int]:
    """Сложить одноимённые счётчики всех проектов в один словарь."""
    total: dict[str, int] = {}
    for item in counters:
        for name, count in item.items():
            total[name] = total.get(name, 0) + count
    return total


def _row_repair(plan: UpdatePlan) -> tuple[str, str, str, str]:
    if plan.skip.repair:
        return (_PHASE_D, _skipped("--skip-repair"), _NOT_APPLICABLE, _NOT_APPLICABLE)
    curable = _counters([project.curable for project in plan.repair])
    action = " · ".join(f"{name} {count}" for name, count in curable.items() if count)
    if plan.skip.links:
        action = f"{action or 'находок нет'} (link backfill выключен: --skip-links)"
    return (
        _PHASE_D,
        action or "находок нет",
        f"проектов: {len(plan.repair)}",
        _D_REVERSIBLE,
    )


def _render_plan(plan: UpdatePlan, *, service_count: int, out: Console) -> None:
    """План таблицей плюс то, что обязан прочитать отвечающий на вопрос."""
    table = Table(title="План обновления", show_header=True, box=None, padding=(0, 1))
    table.add_column("Фаза", style="cyan", no_wrap=True)
    # Путь свапа обрезать нельзя: человек одобряет подмену конкретного каталога.
    table.add_column("Действие", overflow="fold")
    table.add_column("Объём")
    table.add_column("Обратимо")
    for row in (
        _row_install(plan),
        _row_migrate(plan),
        _row_services(plan, service_count=service_count),
        _row_repair(plan),
    ):
        table.add_row(*row)
    out.print(table)

    reported = _counters([project.reported_only for project in plan.repair])
    shown = " · ".join(f"{name} {count}" for name, count in reported.items() if count)
    if shown:
        out.print(f"Только докладываются: {shown}")
    if plan.hub_projects:
        out.print(
            "[yellow]Общая hub-БД: "
            + ", ".join(plan.hub_projects)
            + " — миграция такой базы задевает всех её потребителей.[/yellow]"
        )
    out.print(f"[yellow]{_ROLLBACK_KEEPS_DB}[/yellow]")


# ── отчёт ───────────────────────────────────────────────────────────────────


def _install_dict(install: InstallReport | dict[str, Any] | None) -> dict[str, Any]:
    """Отчёт фазы A словарём: у родителя это dataclass, у ребёнка — чужой JSON."""
    if install is None:
        return {}
    if isinstance(install, dict):
        return install
    return asdict(install)


def _render_version_rows(rows: Sequence[dict[str, Any]], out: Console) -> None:
    """Версии установок из словарей — в отчёте ребёнка dataclass'ов уже нет."""
    if not rows:
        return
    table = Table(title="установки cod-doc на машине", show_header=True, box=None, padding=(0, 1))
    table.add_column("Установка", style="cyan", no_wrap=True)
    table.add_column("Версия", overflow="fold")
    table.add_column("Бинарь", style="dim", overflow="fold")
    for row in rows:
        reported = row.get("version")
        shown = short_version(str(reported)) if reported else (row.get("error") or _NOT_APPLICABLE)
        table.add_row(str(row.get("name", "?")), str(shown), str(row.get("binary", "")))
    out.print(table)


def _render_install(report: UpdateReport, out: Console) -> None:
    """Что сделала фаза A: сборка, судьба свапа, версии установок."""
    install = _install_dict(report.install)
    build = install.get("build") or {}
    swap = install.get("swap") or {}
    if build:
        sha = str(build.get("sha") or "?")[:SHA_SHOWN]
        out.print(f"Сборка: {build.get('version') or '?'} ({build.get('ref') or '?'} · {sha})")
    if swap.get("rolled_back"):
        out.print(f"[yellow]Свап откачен; сломанная сборка — {swap.get('broken')}[/yellow]")
    elif swap.get("previous"):
        out.print(f"[dim]Прежний рантайм: {swap['previous']} (cod-doc runtime rollback)[/dim]")
    rows = list(install.get("versions") or [])
    path_tool = install.get("path_tool")
    if path_tool:
        rows.append(path_tool)
    _render_version_rows(rows, out)


def _render_migrate(results: Sequence[MigrateResult], out: Console) -> None:
    if not results:
        return
    failed = [result for result in results if not result.ok]
    out.print(f"Миграции: {len(results) - len(failed)} из {len(results)}")
    for result in failed:
        out.print(f"[red]✗ {result.name}: {result.error}[/red]")


def _render_repair(results: Sequence[RepairResult], out: Console) -> None:
    for result in results:
        out.print(
            f"Починка {result.project}: применено {result.applied} из {len(result.actions)}"
            + (f", ошибок {len(result.errors)}" if result.errors else "")
        )
        for error in result.errors:
            out.print(f"[red]  ✗ {error}[/red]")


def _render_report(report: UpdateReport, out: Console) -> None:
    """Исход прогона: фазы, фаза A подробнее, миграции и починка."""
    table = Table(title="Обновление", show_header=True, box=None, padding=(0, 1))
    table.add_column("Фаза", style="cyan", no_wrap=True)
    table.add_column("Итог")
    table.add_column("Время")
    table.add_column("Детали")
    for phase in report.phases:
        verdict = "пропущена" if phase.skipped else ("ok" if phase.ok else "ошибка")
        duration = f"{phase.duration_s:.1f} с" if phase.duration_s else _NO_DURATION
        table.add_row(phase.name, verdict, duration, phase.skip_reason or phase.error or "")
    out.print(table)
    _render_install(report, out)
    _render_migrate(report.migrate, out)
    _render_repair(report.repair, out)
    if report.rolled_back:
        out.print("[red]Свап откачен — обновление не состоялось.[/red]")


# ── потоки ──────────────────────────────────────────────────────────────────


def _echo_payload(payload: dict[str, object]) -> None:
    """Ровно один объект в stdout и только через ``click.echo`` (ADO-176)."""
    click.echo(_json.dumps(payload, ensure_ascii=False, indent=2))


def _phases_ahead(skip: SkipFlags, *, swapped: bool) -> list[str]:
    """Фазы, которые сейчас действительно исполнятся.

    ``swapped`` отделяет родителя до свапа от процесса, который уже за
    границей: родитель делает только фазу A и relay (фазы B–D исполнит и
    доложит новый бинарь), а после свапа фазу C делать уже есть за чем.
    """
    if not swapped and not skip.install:
        return [_AHEAD_INSTALL, _AHEAD_RELAY]
    ahead: list[str] = []
    if not skip.migrate:
        ahead.append(_AHEAD_MIGRATE)
    if swapped:
        ahead.append(_AHEAD_SERVICES)
    if not skip.repair:
        ahead.append(_AHEAD_REPAIR)
    return ahead


def _announce(steps: Sequence[str]) -> None:
    """Прогресс — в stderr: под ``--json`` в stdout ровно один объект."""
    if steps:
        click.echo("→ " + "; ".join(steps), err=True)


def _plan_steps(skip: SkipFlags) -> list[str]:
    """Что сделает ``plan()``: ревизию он трогает без ``--skip-install``,
    диагноз собирает без ``--skip-repair``."""
    steps: list[str] = []
    if not skip.install:
        steps.append(_PLAN_REF)
    if not skip.repair:
        steps.append(_PLAN_DIAGNOSE)
    return [f"план: {', '.join(steps)}"] if steps else []


def _require_known_projects(cfg: Config, slugs: Sequence[str]) -> None:
    """Неизвестный ``-p`` — ошибка вызова, а не молча сузившийся объём работ."""
    unknown = [slug for slug in slugs if cfg.get_project(slug) is None]
    if unknown:
        raise click.UsageError(
            "нет в реестре ~/.cod-doc/config.yaml: "
            + ", ".join(unknown)
            + " (список — cod-doc project list)"
        )


def _read_payload(source: str) -> dict[str, Any]:
    """Payload родителя: ``-`` — stdin, иначе файл."""
    from cod_doc.services import update_service

    try:
        raw = (
            sys.stdin.read()
            if source == update_service.RESUME_STDIN
            else Path(source).read_text(encoding="utf-8")
        )
    except OSError as exc:
        raise click.ClickException(f"--resume-json: не читается {source}: {exc}") from exc
    try:
        payload = _json.loads(raw or "{}")
    except ValueError as exc:
        raise click.ClickException(f"--resume-json: не разбирается JSON родителя: {exc}") from exc
    if not isinstance(payload, dict):
        raise click.ClickException("--resume-json: ожидался объект JSON")
    return payload


def _install_services(
    cfg: Config, report: UpdateReport, *, enabled: bool
) -> list[ServiceStatus] | None:
    """``--install-services``: фаза C делает kickstart, а он не создаёт plist.

    Отдельным шагом после прогона, а не вместо фазы C: флаг не форвардится
    дочернему процессу (контракт ``update_service._argv_tail``), поэтому
    единственное место, где его исполняет родитель, — после relay. Нужен
    ровно тогда, когда сервисов ещё нет или plist изменился: kickstart в этом
    случае отвечает «не загружен», и фаза C честно докладывает пропуск.
    """
    if not enabled or report.rolled_back:
        return None
    from cod_doc.services import launchd_service

    click.echo("→ install: рендер plist и bootstrap сервисов", err=True)
    return launchd_service.install(
        runtime=runtime_dir(),
        api_port=cfg.api_port,
        workdir=workdir(),
        logs=logs_dir(),
        agents=agents_dir(),
    )


def _emit(
    plan: UpdatePlan | None,
    report: UpdateReport,
    statuses: list[ServiceStatus] | None,
    *,
    options: _Options,
    out: Console,
) -> None:
    if options.as_json:
        _echo_payload(
            {
                "plan": plan.as_dict() if plan is not None else None,
                "report": report.to_dict(),
                "services": [asdict(status) for status in statuses] if statuses else None,
            }
        )
        return
    _render_report(report, out)
    if statuses:
        render_services(statuses, out)


def _interactive() -> bool:
    """Есть ли кому отвечать на вопрос.

    Отдельной функцией, а не выражением по месту: под ``CliRunner`` (и под
    launchd) ``sys.stdin`` подменён, и тестам нужна одна точка подмены — та же,
    которую читает команда.
    """
    return sys.stdin.isatty()


def _approved(plan: UpdatePlan, options: _Options, *, service_count: int, out: Console) -> bool:
    """Единственный вопрос прогона. Обратимый план не спрашивают вовсе."""
    if not plan.has_irreversible:
        return True
    _render_plan(plan, service_count=service_count, out=out)
    if options.yes:
        return True
    if not _interactive():
        raise click.UsageError(_NEEDS_CONSENT)
    return click.confirm(_CONFIRM, default=False)


def _resume(cfg: Config, options: _Options, *, source: str) -> int:
    """Дочерний процесс: фазы B–D новым кодом, отчёт — в stderr."""
    from cod_doc.services import update_service

    payload = _read_payload(source)
    _announce(_phases_ahead(options.skip, swapped=True))
    report = update_service.run_post_swap(
        cfg,
        resumed=payload,
        projects=options.projects or None,
        ttl_minutes=options.ttl_minutes,
        skip=options.skip,
    )
    statuses = _install_services(cfg, report, enabled=options.install_services)
    _emit(None, report, statuses, options=options, out=err_console)
    return report.exit_code


def _full(cfg: Config, options: _Options) -> int:
    """Полный цикл: план → подтверждение → фаза A → relay."""
    from cod_doc.services import launchd_service, runtime_service, update_service

    if options.as_json and not (options.yes or options.dry_run):
        raise click.UsageError(_JSON_NEEDS_CONSENT)
    _require_known_projects(cfg, options.projects)

    out = err_console if options.as_json else console
    _announce(_plan_steps(options.skip))
    try:
        plan = update_service.plan(
            cfg,
            ref=options.ref,
            runtime=runtime_dir(),
            repo=options.repo,
            projects=options.projects or None,
            ttl_minutes=options.ttl_minutes,
            skip=options.skip,
        )
    except runtime_service.RuntimeError_ as exc:
        raise click.ClickException(str(exc)) from exc

    service_count = len(launchd_service.SERVICES)
    if options.dry_run:
        _render_plan(plan, service_count=service_count, out=out)
        if options.as_json:
            _echo_payload({"plan": plan.as_dict(), "report": None, "services": None})
        return update_service.EXIT_OK

    if not _approved(plan, options, service_count=service_count, out=out):
        out.print("[yellow]Отменено — ничего не менялось.[/yellow]")
        return update_service.EXIT_OK

    _announce(_phases_ahead(plan.skip, swapped=False))
    try:
        report = update_service.run(cfg, update_plan=plan, dry_run=False)
    except update_service.UpdateLocked as exc:
        raise click.ClickException(str(exc)) from exc

    statuses = _install_services(cfg, report, enabled=options.install_services)
    _emit(plan, report, statuses, options=options, out=out)
    return report.exit_code


# ── команда ─────────────────────────────────────────────────────────────────


@click.command("update")
@click.option("--ref", default=None, help="Ревизия или ветка; по умолчанию — ветка remote'а")
@click.option("--python", "python_version", default=None, help=f"Версия Python (${PYTHON_ENV})")
@click.option(
    "--repo",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Чекаут репозитория ($COD_DOC_REPO → ~/Git/_my/cod-doc → клон в ~/.cod-doc/src)",
)
@click.option(
    "--skip-install", is_flag=True, default=False, help="Не пересобирать рантайм (фаза A)"
)
@click.option("--skip-migrate", is_flag=True, default=False, help="Не догонять схему (фаза B)")
@click.option("--skip-repair", is_flag=True, default=False, help="Не чинить проекты (фаза D)")
@click.option(
    "--skip-links", is_flag=True, default=False, help="Без link backfill — он самый долгий"
)
@click.option(
    "--install-services",
    is_flag=True,
    default=False,
    help="После свапа не kickstart, а install: отрендерить plist'ы и загрузить",
)
@click.option(
    # Дест намеренно единственного числа, как на остальных 88 командах: по нему
    # zsh-дополнение находит источник значений (`sources.PARAM_SOURCES`), и
    # `projects` осталось бы без слагов в подсказке.
    "--project",
    "-p",
    multiple=True,
    help="Ограничить фазы B и D этим проектом; повторяемый (фаза A машинная)",
)
@click.option("--ttl-minutes", default=None, type=int, help="TTL протухшего замка для фазы D")
@click.option("--dry-run", is_flag=True, default=False, help="Показать план и выйти; не спрашивает")
@click.option("-y", "--yes", is_flag=True, default=False, help="Не спрашивать подтверждения")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON (нужен --yes)")
@click.option(
    "--resume-json", default=None, hidden=True, help="Внутренний вход relay ('-' = stdin)"
)
@click.pass_context
def update(
    ctx: click.Context,
    ref: str | None,
    python_version: str | None,
    repo: Path | None,
    skip_install: bool,
    skip_migrate: bool,
    skip_repair: bool,
    skip_links: bool,
    install_services: bool,
    project: tuple[str, ...],
    ttl_minutes: int | None,
    dry_run: bool,
    yes: bool,
    as_json: bool,
    resume_json: str | None,
) -> None:
    """Обновить установку cod-doc: рантайм, схему, сервисы и состояние проектов.

    Голый вызов — полный цикл по всем проектам реестра с одним подтверждением.
    Код выхода: 0 — всё удалось, 1 — что-то из фаз провалилось, 2 — так звать
    нельзя, 3 — свап откачен и машина осталась на прежней версии.
    """
    from cod_doc.services import repair_service, update_service

    cfg: Config = ctx.obj["config"]
    if python_version:
        # Ни plan(), ни run() версию питона не принимают: рантайм собирает
        # runtime_service, а он берёт её из окружения (_python_version).
        os.environ[PYTHON_ENV] = python_version

    options = _Options(
        ref=ref,
        repo=repo,
        projects=list(project),
        ttl_minutes=ttl_minutes if ttl_minutes is not None else repair_service.DEFAULT_TTL_MINUTES,
        skip=update_service.SkipFlags(
            install=skip_install, migrate=skip_migrate, repair=skip_repair, links=skip_links
        ),
        install_services=install_services,
        as_json=as_json,
        yes=yes,
        dry_run=dry_run,
    )
    if resume_json is not None:
        sys.exit(_resume(cfg, options, source=resume_json))
    sys.exit(_full(cfg, options))
