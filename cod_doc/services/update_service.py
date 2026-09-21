"""ADO-192: оркестратор ``cod-doc update`` — четыре фазы одной командой.

::

    A. свап рантайма  →  B. миграции НОВЫМ бинарём  →  C. рестарт  →  D. починка

Порядок отличается от ``deploy/launchd/cod-doc-services.sh`` осознанно. Скрипт
перезапускает сервисы сразу после свапа, а миграции оставляет человеку — и
открывает окно, в котором демоны уже на новом коде, а схема ещё старая
(``SchemaMismatchError`` в лоб, веб отвечает 503). Здесь демоны поднимаются
уже на догнанной схеме.

Самозамена
==========

Миграции **обязан** катить новый код: ``alembic upgrade head`` старого
процесса накатит старую голову. А процесс, переживший подмену своего же venv,
неизбежно становится химерой: ``mv`` — это rename, уже импортированные модули
целы, но `sys.path` хранит абсолютный путь к ``site-packages`` рантайма, и
любой **последующий** импорт придёт уже из нового дерева. Ленивые импорты в
CLI при этом обязательны (ADO-179) — то есть ровно тот код, который исполнится
после свапа, импортирует по требованию.

Отсюда subprocess-relay::

    P0 (любая установка)
     ├─ план + подтверждение + диагноз фазы D      ← старый код, всё можно
     ├─ runtime_service.install_runtime()          ← ГРАНИЦА
     └─ subprocess.run([<runtime>/bin/cod-doc, "update", "--resume-json", "-"])
            └─ P1: новый интерпретатор, новый код — фазы B, C, D и код выхода

Фаза C (``launchd_service.restart``) живёт в P1, а не в P0 между свапом и
relay: kickstart в родителе поднял бы демонов на новом коде ДО миграций — ровно
то окно «новый код, старая схема», ради закрытия которого переставлен порядок
фаз. Родитель после свапа не делает ничего, кроме relay.

Инвариант, который делает это безопасным: **между swap() и subprocess.run()
процесс импортирует только stdlib**. Поэтому ``runtime_service`` и
``launchd_service`` stdlib-only целиком, а этот модуль, наоборот, импортирует
всё на уровне модуля — ленивый импорт здесь был бы импортом через границу
свапа. Обе стороны стережёт ``tests/services/test_swap_boundary_imports.py``.

``os.execv`` отвергнут: он убивает возможность откатиться — exec в сломанную
сборку означает, что процесс апгрейда просто умирает и восстанавливать
некому. Relay оставляет родителя живым супервизором.

Relay ⟺ свап состоялся. При ``--skip-install`` relay не нужен (P0 и есть
правильный код, и фазы B–D он делает сам). При откате не нужен (``runtime``
снова прежнее дерево, тот же инод). При удачном свапе — всегда, даже с
``--skip-migrate --skip-repair``: ~200 мс за одну ветку кода вместо двух.

Что едет через границу
======================

Родитель кладёт на stdin дочернего процесса JSON формы
``dataclasses.asdict(InstallReport)`` плюс ключ ``src`` — путь временного
worktree сборки, из которого ребёнок (и только он) догоняет установку в PATH
через ``uv tool install``. Ребёнок не разбирает этот словарь: он кладёт его в
``UpdateReport.install`` как есть и дописывает туда свои ключи ``services`` и
``path_tool``. Отчёт между ревизиями может измениться, и валидацией падать
здесь нельзя — падение означало бы, что новый бинарь не умеет доделать
обновление, которое сам же и получил.

Остальное едет аргументами командной строки — их разбирает CLI дочернего
процесса и передаёт в :func:`run_post_swap`: ``--project <slug>`` (повторяемый,
ровно те проекты, что человек одобрил), ``--ttl-minutes <N>``,
``--skip-migrate``, ``--skip-repair``, ``--skip-links``. ``--skip-install``
не форвардится: ребёнок и так в resume-режиме, свап уже позади.

Почему этого нет на MCP
=======================

Закон «четырёх равных поверхностей» требует MCP-зеркала для write-функции
сервиса. Выставлена только фаза D (``project_repair``). Фазы A–C — нет, по
убыванию силы аргумента:

1. Фаза A убивает демона, обслуживающего вызов: ``mv`` рантайма плюс
   ``launchctl kickstart -k com.cod-doc.mcp`` завершает ровно тот процесс,
   который исполняет тул. Клиент получит обрыв транспорта, а не результат.
2. Проверить исход нечем — поверхность, через которую проверяют, и есть
   подменяемая.
3. У фазы A нет проектного скоупа, а контракт ``mcp/tools/_db.py`` —
   «``project`` обязателен в каждом DB-туле».
4. Фаза B из MCP-тула — ровно тот баг, ради которого построен relay: тул по
   построению исполняется старым кодом и накатит старую голову.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cod_doc.config import config_dir
from cod_doc.infra.db import SchemaMismatchError, db_for_entry, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import launchd_service, project_service, repair_service, runtime_service

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config, ProjectEntry
    from cod_doc.services.project_service import MigrateResult

__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_ROLLED_BACK",
    "EXIT_USAGE",
    "InstallReport",
    "PhaseReport",
    "SkipFlags",
    "UpdateLocked",
    "UpdatePlan",
    "UpdateReport",
    "plan",
    "relay_to_new_runtime",
    "run",
    "run_post_swap",
    "update_lock",
]

#: Коды выхода. 3 отделён от 1 осознанно: для автоматики «ничего не
#: изменилось, плохая сборка» и «половина сделана» — разные ситуации.
#: 78 не переиспользуем — его занял launchd под `bad interpreter`.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ROLLED_BACK = 3

#: Имена фаз — контракт отчёта и JSON.
PHASE_INSTALL = "install"
PHASE_MIGRATE = "migrate"
PHASE_SERVICES = "services"
PHASE_REPAIR = "repair"

#: Пятая строка отчёта — не фаза, а делегирование фаз B–D дочернему процессу.
#: Его собственный отчёт печатается им же; сюда доезжает только код выхода.
PHASE_RELAY = "relay"

#: Фазы, которые исполняются после свапа — их и пропускают, если свап не удался.
_POST_SWAP_PHASES: tuple[str, ...] = (PHASE_MIGRATE, PHASE_SERVICES, PHASE_REPAIR)

#: Аргументы, которыми родитель зовёт новый бинарь.
UPDATE_COMMAND = "update"
RESUME_FLAG = "--resume-json"
RESUME_STDIN = "-"

#: Флаги, форвардящиеся дочернему процессу. Имена — контракт с CLI: команда
#: собирает argv сама, потому что relay зовёт именно она, а не человек.
FLAG_PROJECT = "--project"
FLAG_TTL_MINUTES = "--ttl-minutes"
FLAG_SKIP_MIGRATE = "--skip-migrate"
FLAG_SKIP_REPAIR = "--skip-repair"
FLAG_SKIP_LINKS = "--skip-links"

#: Ключи payload'а сверх формы ``asdict(InstallReport)``.
_KEY_SRC = "src"
_KEY_SERVICES = "services"
_KEY_PATH_TOOL = "path_tool"

#: Имя установки в PATH — то же, что у ``runtime_service.installed_versions``.
PATH_TOOL_NAME = "PATH (uv tool)"

#: Файл блокировки: два `update` разом (или `update` во время `routine tick`)
#: переплетаются на одной БД и одном каталоге рантайма.
LOCK_FILENAME = "update.lock"

#: Автор записей фазы D — тот же, что у ``repair_service.apply`` по умолчанию.
REPAIR_AUTHOR = "cli:update"

#: Причины пропуска фаз. Пустое место в колонке «почему» заставляет человека
#: гадать, отработала фаза вхолостую или её выключили.
_REASON_DRY_RUN = "dry-run: план показан, ничего не менялось"
_REASON_SKIP_INSTALL = "--skip-install: рантайм не пересобирается"
_REASON_SKIP_MIGRATE = "--skip-migrate: схема не догоняется"
_REASON_SKIP_REPAIR = "--skip-repair: состояние проектов не чинится"
_REASON_NO_SWAP = "рантайм не менялся — перезапускать нечего"
_REASON_NO_SERVICES = "ни один сервис не загружен в launchd (cod-doc services install)"
_REASON_ROLLED_BACK = "свап откачен: старый код на месте, догонять схему под ним нельзя"
_REASON_INSTALL_FAILED = "фаза A не удалась — дальше идти не с чем"

#: Диагнозы, попадающие человеку в отчёт.
_ERR_INCOMPLETE_PLAN = "план без ревизии или без пути к рантайму — пересобери его через plan()"
_ERR_UNKNOWN_PROJECT = "нет в реестре ~/.cod-doc/config.yaml"
_ERR_NOT_IN_DB = "проект не зарегистрирован в БД (cod-doc project add)"
_ERR_NO_BUILD_SRC = "исходников сборки уже нет — установка в PATH не догнана"
SCHEMA_BEHIND_HINT = "схема отстала, прогони без --skip-migrate"

#: Что в плане необратимо. Свап откатывается (`runtime_service.rollback`),
#: `uv tool install` в PATH — нет; импорт документа и снятие замка — тоже нет.
_IRREVERSIBLE_REPAIR_KINDS = frozenset(
    {repair_service.KIND_DOC_IMPORT, repair_service.KIND_RELEASE_STALE}
)

log = logging.getLogger("cod_doc.services.update_service")


class UpdateLocked(RuntimeError):
    """``~/.cod-doc/update.lock`` держит другой прогон."""


@dataclass(slots=True)
class SkipFlags:
    """Какие фазы пропустить. ``skip_links`` режет только самую дорогую чинилку."""

    install: bool = False
    migrate: bool = False
    repair: bool = False
    links: bool = False


@dataclass(slots=True)
class PhaseReport:
    """Исход одной фазы — для таблицы и для кода выхода."""

    name: str
    ok: bool
    skipped: bool = False
    skip_reason: str | None = None
    error: str | None = None
    duration_s: float = 0.0


@dataclass(slots=True)
class InstallReport:
    """Фаза A целиком: сборка, свап, сервисы, версии установок.

    Форма этого dataclass'а — она же форма JSON, который родитель кладёт на
    stdin дочернего процесса. Поэтому ``services`` и ``path_tool`` заполняет
    P1: у него отчёт живёт словарём той же формы (см. «Что едет через
    границу»), собрать сюда dataclass он права не имеет — это была бы
    валидация чужой ревизии.
    """

    build: runtime_service.BuildReport | None = None
    swap: runtime_service.SwapReport | None = None
    services: list[launchd_service.ServiceStatus] = field(default_factory=list)
    versions: list[runtime_service.InstallVersion] = field(default_factory=list)
    path_tool: runtime_service.InstallVersion | None = None


@dataclass(slots=True)
class UpdatePlan:
    """Одобряемый человеком план: классы действий и их объём, не поимённый список.

    Считается СТАРЫМ кодом до фазы A — реестр проектов известен, а
    ``repair_service.diagnose`` read-only. Дочерний процесс перед применением
    диагностирует заново новым кодом: план для человека, диагноз для машины.

    ``target_version`` до сборки неизвестен (версию называет собранный venv,
    ``BuildReport.version``) и остаётся ``None``.
    """

    ref: str
    target_sha: str | None = None
    target_version: str | None = None
    current_version: str | None = None
    runtime: Path | None = None
    repo: Path | None = None
    projects: list[str] = field(default_factory=list)
    hub_projects: list[str] = field(default_factory=list)
    repair: list[repair_service.RepairPlan] = field(default_factory=list)
    skip: SkipFlags = field(default_factory=SkipFlags)
    ttl_minutes: int = repair_service.DEFAULT_TTL_MINUTES

    @property
    def has_irreversible(self) -> bool:
        """Есть ли в плане хоть одно необратимое действие — предмет подтверждения."""
        if not self.skip.install:
            return True
        if not self.skip.migrate and self.projects:
            return True
        return any(
            action.kind in _IRREVERSIBLE_REPAIR_KINDS
            for project in self.repair
            for action in project.actions
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe снимок плана — для ``--json`` и для печати из CLI."""
        return {
            "ref": self.ref,
            "target_sha": self.target_sha,
            "target_version": self.target_version,
            "current_version": self.current_version,
            "runtime": str(self.runtime) if self.runtime is not None else None,
            "repo": str(self.repo) if self.repo is not None else None,
            "projects": list(self.projects),
            "hub_projects": list(self.hub_projects),
            "repair": [project.as_dict() for project in self.repair],
            "skip": asdict(self.skip),
            "ttl_minutes": self.ttl_minutes,
            "has_irreversible": self.has_irreversible,
        }


@dataclass(slots=True)
class UpdateReport:
    """Полный отчёт прогона. Рендерится и в rich-таблицы, и в один JSON-объект."""

    dry_run: bool = False
    phases: list[PhaseReport] = field(default_factory=list)
    install: InstallReport | dict[str, Any] | None = None
    migrate: list[MigrateResult] = field(default_factory=list)
    repair: list[repair_service.RepairResult] = field(default_factory=list)
    rolled_back: bool = False
    #: Код выхода дочернего процесса, если relay состоялся. Отдельное поле, а не
    #: вывод из фаз: фазы B–D исполнил он, и его код — единственное, что о них
    #: знает родитель.
    child_exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe снимок: ни Path, ни datetime, ни dataclass наружу не уходят."""
        return {
            "dry_run": self.dry_run,
            "rolled_back": self.rolled_back,
            "exit_code": self.exit_code,
            "child_exit_code": self.child_exit_code,
            "phases": [asdict(phase) for phase in self.phases],
            "install": _install_as_dict(self.install),
            "migrate": [asdict(result) for result in self.migrate],
            "repair": [result.as_dict() for result in self.repair],
        }

    @property
    def exit_code(self) -> int:
        """Код выхода по совокупности фаз (см. константы EXIT_*)."""
        if self.rolled_back:
            return EXIT_ROLLED_BACK
        if self.child_exit_code is not None:
            return self.child_exit_code
        if any(not phase.ok for phase in self.phases):
            return EXIT_FAILED
        return EXIT_OK


def plan(
    cfg: Config,
    *,
    ref: str | None,
    runtime: Path,
    repo: Path | None,
    projects: list[str] | None,
    ttl_minutes: int = repair_service.DEFAULT_TTL_MINUTES,
    skip: SkipFlags | None = None,
) -> UpdatePlan:
    """Собрать план всех фаз. Ничего не меняет и ничего не спрашивает.

    ``runtime_service.resolve_repo`` / ``resolve_ref`` бросают
    ``RuntimeError_`` — «негде взять исходники», «ревизия не предок ветки по
    умолчанию». Наружу они идут как есть: их текст уже называет выход
    (``--repo``, ``$COD_DOC_REPO_URL``, ``--skip-install``), а перевести его в
    пустой план значило бы предложить человеку одобрить обновление, которого
    не будет.
    """
    flags = skip or SkipFlags()
    resolved_repo = repo
    chosen_ref = ref or ""
    target_sha: str | None = None
    if not flags.install:
        resolved_repo = runtime_service.resolve_repo(
            repo=repo,
            # Кэш клона знает только `update_service`: `runtime_service`
            # stdlib-only и `cod_doc.config` импортировать не может.
            src_cache=config_dir() / runtime_service.SRC_CACHE_DIRNAME,
        )
        chosen_ref = ref or runtime_service.default_branch(resolved_repo)
        target_sha = runtime_service.resolve_ref(resolved_repo, chosen_ref)

    selected = _slugs(cfg, projects)
    update_plan = UpdatePlan(
        ref=chosen_ref,
        target_sha=target_sha,
        current_version=_current_version(runtime, resolved_repo),
        runtime=runtime,
        repo=resolved_repo,
        projects=selected,
        hub_projects=_hub_projects(cfg, selected),
        skip=flags,
        ttl_minutes=ttl_minutes,
    )
    if not flags.repair:
        update_plan.repair = _diagnose_all(cfg, selected, ttl_minutes=ttl_minutes)
    return update_plan


def run(cfg: Config, *, update_plan: UpdatePlan, dry_run: bool = False) -> UpdateReport:
    """Провести фазу A, затем передать управление новому бинарю.

    Фаз B–D здесь нет намеренно: после свапа родитель делает только relay
    (см. «Самозамена»). Без свапа (``--skip-install``) он же их и исполняет —
    тем же :func:`run_post_swap`, потому что тогда P0 и есть правильный код.

    Возвращает отчёт только если relay не понадобился (``--skip-install``,
    откат или ``dry_run``); иначе вызывающий обязан выйти с кодом дочернего
    процесса, который вернул :func:`relay_to_new_runtime`. Этот код лежит в
    ``UpdateReport.child_exit_code`` и попадает в ``exit_code``, так что
    вызывающему достаточно обычного ``sys.exit(report.exit_code)``.
    """
    if dry_run:
        return _dry_run_report()
    with update_lock():
        if update_plan.skip.install:
            # Свапа нет — значит, и relay нет: этот процесс и есть правильный
            # код, фазы B–D он делает сам.
            return run_post_swap(
                cfg,
                resumed={},
                projects=update_plan.projects,
                ttl_minutes=update_plan.ttl_minutes,
                skip=update_plan.skip,
            )
        return _install_and_relay(update_plan)


def run_post_swap(
    cfg: Config,
    *,
    resumed: dict[str, Any],
    projects: list[str] | None,
    ttl_minutes: int = repair_service.DEFAULT_TTL_MINUTES,
    skip: SkipFlags | None = None,
) -> UpdateReport:
    """Точка входа дочернего процесса: фазы B, C и D новым кодом.

    ``resumed`` — непрозрачный JSON от родителя. P0 старый, P1 новый, и если
    dataclass отчёта между ревизиями изменился, валидацией здесь падать
    нельзя: dict кладётся в ``report.install`` как есть.

    Блокировку не берёт: её держит родитель на всё время relay, и повторный
    захват был бы отказом самому себе. Единственный вызывающий, у которого
    родителя нет, — :func:`run` с ``--skip-install``, и он уже под локом.
    """
    flags = skip or SkipFlags()
    # Пустой список от CLI значит то же, что его отсутствие: весь реестр.
    # Иначе фаза B молчала бы, а фаза D чинила всё — разъехавшийся скоуп.
    selected = projects or None
    swapped = bool(resumed)
    install = dict(resumed)  # копия: чужой словарь не мутируем
    report = UpdateReport(install=install if swapped else None)

    if swapped:
        report.phases.append(PhaseReport(name=PHASE_INSTALL, ok=True))
        install[_KEY_PATH_TOOL] = asdict(_sync_path_tool(install.get(_KEY_SRC)))
    else:
        report.phases.append(_skipped(PHASE_INSTALL, _REASON_SKIP_INSTALL))

    report.phases.append(_migrate_phase(cfg, report, projects=selected, skip=flags))
    services_phase, statuses = _services_phase(cfg, swapped=swapped)
    if statuses:
        install[_KEY_SERVICES] = [asdict(status) for status in statuses]
    report.phases.append(services_phase)
    report.phases.append(
        _repair_phase(cfg, report, projects=selected, ttl_minutes=ttl_minutes, skip=flags)
    )
    return report


def relay_to_new_runtime(
    *,
    runtime: Path,
    payload: dict[str, Any],
    argv_tail: list[str],
) -> subprocess.CompletedProcess[str]:
    """Запустить ``<runtime>/bin/cod-doc update --resume-json -`` и отдать его код.

    Console-script рантайма — sh-обёртка
    (``exec "$(dirname -- "$(realpath -- "$0")")"/python "$0" "$@"``), поэтому
    после свапа он резолвит уже новый интерпретатор. Зовётся **по пути
    рантайма**, а не через ``sys.executable``: в этом весь смысл relay.

    ``uv tool install --force`` тоже делает дочерний процесс — он живёт в
    ``~/.cod-doc/runtime``, и подмена ``~/.local/share/uv/tools/cod-doc`` для
    него безобидна, тогда как для родителя, запущенного из
    ``~/.local/bin/cod-doc``, это была бы вторая самозамена в одном прогоне.

    stdout/stderr наследуются, а не перехватываются: отчёт печатает ребёнок, и
    буферизовать его до конца прогона значило бы показать пустой экран на всё
    время миграций. Таймаута нет по той же причине — потолок здесь означал бы
    убитую на середине миграцию.
    """
    binary = runtime / "bin" / "cod-doc"
    argv = [str(binary), UPDATE_COMMAND, RESUME_FLAG, RESUME_STDIN, *argv_tail]
    return subprocess.run(
        argv,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        check=False,
    )


@contextlib.contextmanager
def update_lock(path: Path | None = None) -> Iterator[Path]:
    """Эксклюзивный ``flock`` на ``~/.cod-doc/update.lock`` без ожидания.

    ``LOCK_NB``, а не блокирующий захват: молча ждущий `update` неотличим от
    зависшего, а параллельный прогон — это два alembic на одной БД и два
    ``mv`` одного каталога рантайма. Замок привязан к открытому файлу, поэтому
    снимается и при падении процесса — чинить залипший lock-файл руками не
    придётся.
    """
    lock_path = path if path is not None else config_dir() / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateLocked(
                f"уже идёт другой прогон cod-doc update (замок {lock_path}); "
                "дождись его конца или сними процесс"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        yield lock_path
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


# --------------------------------------------------------------------- #
# Фаза A и relay (родитель)                                              #
# --------------------------------------------------------------------- #


def _install_and_relay(update_plan: UpdatePlan) -> UpdateReport:
    """Собрать и подменить рантайм, затем отдать фазы B–D новому бинарю."""
    report = UpdateReport()
    repo, sha, runtime = update_plan.repo, update_plan.target_sha, update_plan.runtime
    if repo is None or sha is None or runtime is None:
        report.phases.append(PhaseReport(name=PHASE_INSTALL, ok=False, error=_ERR_INCOMPLETE_PLAN))
        report.phases.extend(_skipped(name, _REASON_INSTALL_FAILED) for name in _POST_SWAP_PHASES)
        return report

    phase, install, src = _phase_install(repo, sha, runtime=runtime, ref=update_plan.ref)
    report.install = install
    report.phases.append(phase)
    try:
        if not phase.ok:
            report.rolled_back = install.swap is not None and install.swap.rolled_back
            reason = _REASON_ROLLED_BACK if report.rolled_back else _REASON_INSTALL_FAILED
            report.phases.extend(_skipped(name, reason) for name in _POST_SWAP_PHASES)
            return report
        _relay(report, update_plan, runtime=runtime, install=install, src=src)
        _refresh_versions(install, runtime=runtime, repo=repo)
        return report
    finally:
        # Временный worktree обязан дожить до конца relay: `uv tool install`
        # ставит PATH-установку именно из него, и делает это ребёнок.
        if src is not None:
            runtime_service.drop_build_src(src)


def _refresh_versions(install: InstallReport, *, runtime: Path, repo: Path) -> None:
    """Перечитать версии установок: пока шёл relay, установка в PATH сменилась.

    ``install.versions`` снят в фазе A — до того, как ребёнок выполнил
    ``uv tool install --force`` (:func:`run_post_swap`), поэтому строка
    ``PATH (uv tool)`` в нём заведомо протухшая. Ждать значение от ребёнка
    нечего: свой отчёт он печатает текстом в терминал, а ``--json`` ему не
    форвардится (см. :func:`_argv_tail`) — под ``--json`` отчёт родителя
    вообще единственный, и без этого шага он называл бы версию до
    обновления.

    Своё состояние отчёт при этом не додумывает, а спрашивает: чужой код не
    исполняется, ``installed_versions`` зовёт у бинарей ``--version``.
    Исход relay не важен — после отката в PATH тоже надо смотреть, а не
    угадывать.
    """
    with contextlib.suppress(OSError):
        install.versions = runtime_service.installed_versions(runtime=runtime, repo=repo)


def _phase_install(
    repo: Path,
    sha: str,
    *,
    runtime: Path,
    ref: str,
) -> tuple[PhaseReport, InstallReport, Path | None]:
    """Сборка → свап → проверка → при провале авто-откат (``install_runtime``)."""
    started = time.monotonic()
    install = InstallReport()
    try:
        step = runtime_service.install_runtime(repo, sha, runtime=runtime)
    except (runtime_service.RuntimeError_, OSError) as exc:
        phase = PhaseReport(
            name=PHASE_INSTALL, ok=False, error=str(exc), duration_s=_since(started)
        )
        return phase, install, None

    if step.build is not None and ref:
        # `build_staged` кладёт в `ref` sha — человекочитаемой ревизии в его
        # сигнатуре нет. Знает её только вызывающий.
        step.build.ref = ref
    install.build = step.build
    install.swap = step.swap
    install.versions = step.versions
    src = Path(step.build.src) if step.build is not None else None
    phase = PhaseReport(
        name=PHASE_INSTALL, ok=step.ok, error=step.error, duration_s=_since(started)
    )
    return phase, install, src


def _relay(
    report: UpdateReport,
    update_plan: UpdatePlan,
    *,
    runtime: Path,
    install: InstallReport,
    src: Path | None,
) -> None:
    """Передать управление новому бинарю и снять с него код выхода."""
    payload: dict[str, Any] = {
        **asdict(install),
        _KEY_SRC: str(src) if src is not None else None,
    }
    started = time.monotonic()
    try:
        proc = relay_to_new_runtime(
            runtime=runtime,
            payload=payload,
            argv_tail=_argv_tail(update_plan),
        )
        code = proc.returncode
        error = None if code == EXIT_OK else f"новый бинарь вернул код {code}"
    except OSError as exc:
        code = EXIT_FAILED
        error = f"не запускается {runtime / 'bin' / 'cod-doc'}: {exc}"
    report.child_exit_code = code
    report.rolled_back = code == EXIT_ROLLED_BACK
    report.phases.append(
        PhaseReport(
            name=PHASE_RELAY,
            ok=code == EXIT_OK,
            error=error,
            duration_s=_since(started),
        )
    )


def _argv_tail(update_plan: UpdatePlan) -> list[str]:
    """Флаги, с которыми родитель зовёт ребёнка: одобренный человеком объём работ."""
    tail: list[str] = []
    for slug in update_plan.projects:
        tail += [FLAG_PROJECT, slug]
    tail += [FLAG_TTL_MINUTES, str(update_plan.ttl_minutes)]
    if update_plan.skip.migrate:
        tail.append(FLAG_SKIP_MIGRATE)
    if update_plan.skip.repair:
        tail.append(FLAG_SKIP_REPAIR)
    if update_plan.skip.links:
        tail.append(FLAG_SKIP_LINKS)
    return tail


def _sync_path_tool(src: object) -> runtime_service.InstallVersion:
    """Догнать установку в PATH до собранной ревизии (фаза A, но в ребёнке).

    Неудача здесь не роняет обновление: рантайм сервисов уже подменён и
    проверен, а ``~/.local/bin/cod-doc`` — отдельная установка. Диагноз
    попадает в ``InstallVersion.error``.
    """
    binary = str(Path.home() / ".local" / "bin" / "cod-doc")
    if not isinstance(src, str) or not Path(src).is_dir():
        return runtime_service.InstallVersion(
            name=PATH_TOOL_NAME, binary=binary, version=None, error=_ERR_NO_BUILD_SRC
        )
    try:
        version = runtime_service.sync_path_tool(Path(src))
    except (runtime_service.RuntimeError_, OSError) as exc:
        return runtime_service.InstallVersion(
            name=PATH_TOOL_NAME, binary=binary, version=None, error=str(exc)
        )
    return runtime_service.InstallVersion(name=PATH_TOOL_NAME, binary=binary, version=version)


# --------------------------------------------------------------------- #
# Фазы B, C, D                                                           #
# --------------------------------------------------------------------- #


def _migrate_phase(
    cfg: Config,
    report: UpdateReport,
    *,
    projects: list[str] | None,
    skip: SkipFlags,
) -> PhaseReport:
    """Фаза B. Провал не откатывает свап: старый код на новой схеме — хуже."""
    if skip.migrate:
        return _skipped(PHASE_MIGRATE, _REASON_SKIP_MIGRATE)
    started = time.monotonic()
    report.migrate = _migrate(cfg, projects)
    failed = [result for result in report.migrate if not result.ok]
    return PhaseReport(
        name=PHASE_MIGRATE,
        ok=not failed,
        error="; ".join(f"{result.name}: {result.error}" for result in failed) or None,
        duration_s=_since(started),
    )


def _migrate(cfg: Config, projects: list[str] | None) -> list[MigrateResult]:
    """Тонкая обёртка над ``project_service``: одна упавшая не роняет остальные."""
    if projects is None:
        return project_service.migrate_registered_projects(cfg)
    results: list[MigrateResult] = []
    for slug in projects:
        entry = cfg.get_project(slug)
        if entry is None:
            results.append(
                project_service.MigrateResult(
                    name=slug, db_url="", ok=False, error=_ERR_UNKNOWN_PROJECT
                )
            )
            continue
        results.append(project_service.migrate_entry(entry))
    return results


def _services_phase(
    cfg: Config,
    *,
    swapped: bool,
) -> tuple[PhaseReport, list[launchd_service.ServiceStatus]]:
    """Фаза C: ``kickstart`` всех трёх демонов — уже на догнанной схеме."""
    if not swapped:
        return _skipped(PHASE_SERVICES, _REASON_NO_SWAP), []
    started = time.monotonic()
    statuses = launchd_service.restart(api_port=cfg.api_port)
    unsupported = [status for status in statuses if status.skipped_reason]
    if statuses and len(unsupported) == len(statuses):
        return _skipped(PHASE_SERVICES, unsupported[0].skipped_reason), statuses

    failed = [status for status in statuses if not status.kicked and not status.skipped_reason]
    if statuses and len(failed) == len(statuses):
        # `kickstart` отвечает «не загружен» на каждый лейбл — сервисы под
        # launchd просто не установлены. Это не провал обновления.
        return _skipped(PHASE_SERVICES, _REASON_NO_SERVICES), statuses
    return (
        PhaseReport(
            name=PHASE_SERVICES,
            ok=not failed,
            error="; ".join(status.label for status in failed) or None,
            duration_s=_since(started),
        ),
        statuses,
    )


def _repair_phase(
    cfg: Config,
    report: UpdateReport,
    *,
    projects: list[str] | None,
    ttl_minutes: int,
    skip: SkipFlags,
) -> PhaseReport:
    """Фаза D: починка по каждому проекту; упавший проект не трогает остальные."""
    if skip.repair:
        return _skipped(PHASE_REPAIR, _REASON_SKIP_REPAIR)
    started = time.monotonic()
    report.repair = [
        _repair_one(cfg, slug, ttl_minutes=ttl_minutes) for slug in _slugs(cfg, projects)
    ]
    failed = [result for result in report.repair if not result.ok]
    return PhaseReport(
        name=PHASE_REPAIR,
        ok=not failed,
        error="; ".join(f"{result.project}: {_errors_of(result)}" for result in failed) or None,
        duration_s=_since(started),
    )


def _repair_one(cfg: Config, slug: str, *, ttl_minutes: int) -> repair_service.RepairResult:
    """Починить один проект. Любая его беда — строка в отчёте, а не исключение."""
    entry = cfg.get_project(slug)
    if entry is None:
        return _repair_failure(slug, _ERR_UNKNOWN_PROJECT)
    try:
        factory, engine = db_for_entry(entry)
    except SchemaMismatchError as exc:
        return _repair_failure(slug, f"{SCHEMA_BEHIND_HINT}: {exc}")
    except Exception as exc:
        return _repair_failure(slug, f"БД не открывается: {exc}")
    try:
        return _repair_in_session(factory, entry, slug=slug, ttl_minutes=ttl_minutes)
    except SchemaMismatchError as exc:
        return _repair_failure(slug, f"{SCHEMA_BEHIND_HINT}: {exc}")
    except Exception as exc:
        log.warning("repair %s failed: %s", slug, exc)
        return _repair_failure(slug, str(exc))
    finally:
        engine.dispose()


def _repair_in_session(
    factory: sessionmaker[Session],
    entry: ProjectEntry,
    *,
    slug: str,
    ttl_minutes: int,
) -> repair_service.RepairResult:
    with transactional(factory) as session:
        project_id = _project_id(session, slug)
        if project_id is None:
            return _repair_failure(slug, _ERR_NOT_IN_DB)
        return repair_service.apply(
            session,
            project_id=project_id,
            root_path=entry.root,
            master_path=entry.master_path,
            slug=slug,
            ttl_minutes=ttl_minutes,
            author=REPAIR_AUTHOR,
        )


# --------------------------------------------------------------------- #
# План: проекты и диагноз                                                #
# --------------------------------------------------------------------- #


def _slugs(cfg: Config, projects: list[str] | None) -> list[str]:
    """Проекты: выбранные человеком или весь реестр.

    Неизвестный слаг не отбрасывается — он доедет до фазы, которая скажет о
    нём вслух. Молча сузить объём работ хуже, чем отказаться.
    """
    if projects:
        return list(projects)
    return [entry.name for entry in cfg.list_projects()]


def _hub_projects(cfg: Config, selected: list[str]) -> list[str]:
    """Проекты на общей hub-БД: миграция такой базы задевает всех её потребителей.

    Считается по всему реестру, а не по выбранным: соседа по базе человек мог
    и не называть, но затронет его именно эта миграция.
    """
    registry = cfg.list_projects()
    shared = Counter(entry.db_url for entry in registry if entry.db_url)
    known = {entry.name: entry for entry in registry}
    return [
        slug
        for slug in selected
        if (entry := known.get(slug)) is not None
        and entry.db_url is not None
        and shared[entry.db_url] > 1
    ]


def _diagnose_all(
    cfg: Config, projects: list[str], *, ttl_minutes: int
) -> list[repair_service.RepairPlan]:
    """Диагноз фазы D по каждому проекту. Read-only, ошибки — в лог, не наверх."""
    plans: list[repair_service.RepairPlan] = []
    for slug in projects:
        try:
            diagnosed = _diagnose_one(cfg, slug, ttl_minutes=ttl_minutes)
        except Exception as exc:
            # План — вещь советующая: проект, который сейчас не открывается
            # (отставшая схема, снесённый чекаут), диагностирует заново уже
            # дочерний процесс и он же доложит причину.
            log.warning("diagnose %s skipped: %s", slug, exc)
            continue
        if diagnosed is not None:
            plans.append(diagnosed)
    return plans


def _diagnose_one(cfg: Config, slug: str, *, ttl_minutes: int) -> repair_service.RepairPlan | None:
    entry = cfg.get_project(slug)
    if entry is None:
        return None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory, commit=False) as session:
            project_id = _project_id(session, slug)
            if project_id is None:
                return None
            return repair_service.diagnose(
                session,
                project_id=project_id,
                root_path=entry.root,
                master_path=entry.master_path,
                slug=slug,
                ttl_minutes=ttl_minutes,
            )
    finally:
        engine.dispose()


def _current_version(runtime: Path, repo: Path | None) -> str | None:
    """Версия рантайма сервисов — та, которую сейчас меняем."""
    wanted = str(runtime / "bin" / "cod-doc")
    versions = runtime_service.installed_versions(runtime=runtime, repo=repo)
    return next((entry.version for entry in versions if entry.binary == wanted), None)


# --------------------------------------------------------------------- #
# Мелочи                                                                 #
# --------------------------------------------------------------------- #


def _dry_run_report() -> UpdateReport:
    """Отчёт прогона, которого не было: все четыре фазы пропущены."""
    report = UpdateReport(dry_run=True)
    report.phases = [
        _skipped(name, _REASON_DRY_RUN)
        for name in (PHASE_INSTALL, PHASE_MIGRATE, PHASE_SERVICES, PHASE_REPAIR)
    ]
    return report


def _skipped(name: str, reason: str | None) -> PhaseReport:
    """Пропущенная фаза — не провал: ``ok=True`` плюс причина для таблицы."""
    return PhaseReport(name=name, ok=True, skipped=True, skip_reason=reason)


def _since(started: float) -> float:
    return time.monotonic() - started


def _project_id(session: Session, slug: str) -> int | None:
    row = ProjectRepository(session).get_by_slug(slug)
    return None if row is None else row.row_id


def _repair_failure(slug: str, error: str) -> repair_service.RepairResult:
    return repair_service.RepairResult(project=slug, dry_run=False, errors=[error])


def _errors_of(result: repair_service.RepairResult) -> str:
    """Все жалобы одного проекта одной строкой — и проектные, и поштучные."""
    details = list(result.errors)
    details += [
        f"{action.kind} {action.ref}: {action.error}"
        for action in result.actions
        if action.error is not None
    ]
    return "; ".join(details)


def _install_as_dict(install: InstallReport | dict[str, Any] | None) -> dict[str, Any] | None:
    """Отчёт фазы A словарём — из dataclass'а родителя или из JSON ребёнка."""
    if install is None:
        return None
    if isinstance(install, InstallReport):
        return asdict(install)
    return dict(install)
