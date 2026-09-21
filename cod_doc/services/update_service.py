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
     ├─ launchd_service.restart()                  ← только stdlib
     └─ subprocess.run([<runtime>/bin/cod-doc, "update", "--resume-json", "-"])
            └─ P1: новый интерпретатор, новый код — фазы B и D, отчёт, код выхода

Инвариант, который делает это безопасным: **между swap() и subprocess.run()
процесс импортирует только stdlib**. Поэтому ``runtime_service`` и
``launchd_service`` stdlib-only целиком, а этот модуль, наоборот, импортирует
всё на уровне модуля — ленивый импорт здесь был бы импортом через границу
свапа. Обе стороны стережёт ``tests/services/test_swap_boundary_imports.py``.

``os.execv`` отвергнут: он убивает возможность откатиться — exec в сломанную
сборку означает, что процесс апгрейда просто умирает и восстанавливать
некому. Relay оставляет родителя живым супервизором.

Relay ⟺ свап состоялся. При ``--skip-install`` relay не нужен (P0 и есть
правильный код). При откате не нужен (``runtime`` снова прежнее дерево, тот же
инод). При удачном свапе — всегда, даже с ``--skip-migrate --skip-repair``:
~200 мс за одну ветку кода вместо двух.

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

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cod_doc.services import launchd_service, repair_service, runtime_service
from cod_doc.services.project_service import MigrateResult

if TYPE_CHECKING:
    from cod_doc.config import Config

__all__ = [
    "EXIT_OK",
    "EXIT_ROLLED_BACK",
    "EXIT_USAGE",
    "InstallReport",
    "PhaseReport",
    "SkipFlags",
    "UpdatePlan",
    "UpdateReport",
    "plan",
    "relay_to_new_runtime",
    "run",
    "run_post_swap",
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
    """Фаза A целиком: сборка, свап, сервисы, версии установок."""

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
        raise NotImplementedError


@dataclass(slots=True)
class UpdateReport:
    """Полный отчёт прогона. Рендерится и в rich-таблицы, и в один JSON-объект."""

    dry_run: bool = False
    phases: list[PhaseReport] = field(default_factory=list)
    install: InstallReport | dict[str, Any] | None = None
    migrate: list[MigrateResult] = field(default_factory=list)
    repair: list[repair_service.RepairResult] = field(default_factory=list)
    rolled_back: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe снимок: ни Path, ни datetime, ни dataclass наружу не уходят."""
        raise NotImplementedError

    @property
    def exit_code(self) -> int:
        """Код выхода по совокупности фаз (см. константы EXIT_*)."""
        raise NotImplementedError


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
    """Собрать план всех фаз. Ничего не меняет и ничего не спрашивает."""
    raise NotImplementedError


def run(cfg: Config, *, update_plan: UpdatePlan, dry_run: bool = False) -> UpdateReport:
    """Провести фазы A и C, затем передать управление новому бинарю.

    Возвращает отчёт только если relay не понадобился (``--skip-install``,
    откат или ``dry_run``); иначе вызывающий обязан выйти с кодом дочернего
    процесса, который вернул :func:`relay_to_new_runtime`.
    """
    raise NotImplementedError


def run_post_swap(
    cfg: Config,
    *,
    resumed: dict[str, Any],
    projects: list[str] | None,
    ttl_minutes: int = repair_service.DEFAULT_TTL_MINUTES,
    skip: SkipFlags | None = None,
) -> UpdateReport:
    """Точка входа дочернего процесса: фазы B и D новым кодом.

    ``resumed`` — непрозрачный JSON от родителя. P0 старый, P1 новый, и если
    dataclass отчёта между ревизиями изменился, валидацией здесь падать
    нельзя: dict кладётся в ``report.install`` как есть.
    """
    raise NotImplementedError


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
    """
    raise NotImplementedError
