"""ADO-192: сборка и атомарная подмена пиннованного рантайма `~/.cod-doc/runtime`.

Порт `build_staged` / `cmd_upgrade` / `cmd_rollback` из
``deploy/launchd/cod-doc-services.sh`` (ADO-189/191). Портировать пришлось не
из любви к Python: ``deploy/`` не входит в колесо
(``[tool.setuptools.packages.find] include = ["cod_doc*"]``), поэтому из
установленного рантайма апгрейд был недоступен вовсе — скрипт существовал
только внутри чекаута репозитория.

**Модуль обязан импортировать только stdlib** — и на уровне модуля, и в телах
функций. Это не стиль, а условие корректности: функции отсюда исполняются
в момент, когда ``~/.cod-doc/runtime`` уже подменён, а `sys.path` хранит
абсолютный путь к его ``site-packages``. Любой импорт `cod_doc.*`, `click`,
`rich` или `sqlalchemy` после свапа придёт уже из НОВОГО дерева и смешает в
одном процессе две ревизии. Стережёт
``tests/services/test_swap_boundary_imports.py``.

Прецедент внешнего бинаря из слоя services — :mod:`cod_doc.services.gh_service`:
одна точка ``_run``, свой тип ошибки, поверхности подменяют в тестах одну
функцию, а не три.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "BuildReport",
    "InstallStepReport",
    "InstallVersion",
    "RuntimeError_",
    "SwapReport",
    "build_staged",
    "default_branch",
    "drop_build_src",
    "install_runtime",
    "installed_versions",
    "resolve_ref",
    "resolve_repo",
    "rollback",
    "rollback_failed_swap",
    "swap",
    "sync_path_tool",
    "verify_swapped",
]

#: Дефолтная версия Python для сборки рантайма (переопределяется COD_DOC_PYTHON).
DEFAULT_PYTHON_VERSION = "3.13"

#: Потолок ожидания внешнего бинаря. git fetch по холодной сети бывает долгим,
#: но висеть вечно под launchd команда не должна.
SUBPROCESS_TIMEOUT_S = 600.0

#: Куда клонируется репозиторий, когда локального чекаута нет.
SRC_CACHE_DIRNAME = "src"


class RuntimeError_(RuntimeError):
    """`git`/`uv` отсутствует, вернул ненулевой код или ревизия отвергнута.

    Имя с подчёркиванием — чтобы не затенять встроенный ``RuntimeError`` в
    модуле, который им же и пользуется.
    """


@dataclass(slots=True)
class BuildReport:
    """Результат сборки рантайма в ``<runtime>.staged``."""

    ref: str
    sha: str
    version: str
    src: str
    staged: str
    ok: bool
    error: str | None = None


@dataclass(slots=True)
class SwapReport:
    """Результат подмены рантайма — или её отката."""

    runtime: str
    previous: str | None
    broken: str | None = None
    rolled_back: bool = False
    ok: bool = True
    error: str | None = None


@dataclass(slots=True)
class InstallVersion:
    """Версия одной из установок cod-doc на машине."""

    name: str
    binary: str
    version: str | None
    error: str | None = None


@dataclass(slots=True)
class InstallStepReport:
    """Фаза A целиком: сборка, свап и (при провале) автоматический откат."""

    build: BuildReport | None = None
    swap: SwapReport | None = None
    versions: list[InstallVersion] = field(default_factory=list)
    ok: bool = False
    error: str | None = None


def resolve_repo(*, repo: Path | None, src_cache: Path) -> Path:
    """Найти git-чекаут, из которого собирать, либо склонировать его.

    Порядок: явный ``repo`` → ``$COD_DOC_REPO`` → ``~/Git/_my/cod-doc`` →
    уже склонированный ``src_cache`` → свежий ``git clone --filter=blob:none``.
    Локальный чекаут предпочитается: он быстрее и работает офлайн.
    """
    raise NotImplementedError


def default_branch(repo: Path) -> str:
    """Ветка по умолчанию у remote — через ``git rev-parse --abbrev-ref origin/HEAD``.

    Хардкод ``origin/main`` из bash-скрипта не переносим: у чужого чекаута
    remote может называться иначе. При неудаче — фолбэк на ``origin/main``.
    """
    raise NotImplementedError


def resolve_ref(repo: Path, ref: str) -> str:
    """Развернуть ревизию в sha, отвергнув всё, что не влито в ветку по умолчанию.

    ``git fetch origin --tags --prune``, затем
    ``git merge-base --is-ancestor <sha> <default-branch>``: катится только
    смерженное.
    """
    raise NotImplementedError


def build_staged(
    repo: Path,
    sha: str,
    *,
    runtime: Path,
    python_version: str = DEFAULT_PYTHON_VERSION,
) -> BuildReport:
    """Собрать ревизию в ``<runtime>.staged`` и прогнать smoke-тест ДО свапа.

    Механики, каждая из которых поймана на живой машине:

    - источник — временный ``git worktree add --detach``, а **не**
      ``git archive``: внутри нужен ``.git``, иначе setuptools-scm не выведет
      версию и пришлось бы руками конвертировать ``git describe`` в PEP 440;
    - ``uv venv --relocatable`` **обязателен**: без него путь ``.staged``
      запекается в shebang каждого console-script'а, и после свапа launchd
      роняет демоны с ``bad interpreter`` (код 78). Smoke-тест до свапа этого
      не видит — поэтому есть и второй, после;
    - свежий venv, а не install поверх: install поверх не удаляет модули,
      исчезнувшие из пакета (в рантайме годами жил ``services/story_service.py``
      рядом с пакетом ``services/story_service/``);
    - версия проверяется ``python -P -c 'import cod_doc'``, а не
      ``cod-doc --version``: ``-P`` не пускает cwd в ``sys.path``, иначе
      проверка врёт ровно там, где нужна, а ``--version`` появился только в
      ADO-189 и старую ревизию им не проверить.
    """
    raise NotImplementedError


def drop_build_src(src: Path) -> None:
    """Снять временный worktree. Зовётся и из ``finally`` при провале сборки."""
    raise NotImplementedError


def swap(runtime: Path) -> SwapReport:
    """Атомарно подменить рантайм: ``.previous`` ← текущий, ``.staged`` → рабочий.

    ``mv`` внутри ``~/.cod-doc`` — это rename на одном ФС: иноды не меняются,
    живые демоны продолжают работать на старом дереве до ``kickstart``.
    """
    raise NotImplementedError


def verify_swapped(runtime: Path) -> tuple[bool, str]:
    """Smoke-тест ПОСЛЕ свапа: ``bin/cod-doc-mcp --help``.

    Проверка до свапа не видит поломок самого переезда — прежде всего
    запечённого shebang'а.
    """
    raise NotImplementedError


def rollback_failed_swap(runtime: Path) -> SwapReport:
    """Аварийный откат: рабочий → ``.broken``, ``.previous`` → рабочий.

    Сломанная сборка сохраняется, а не удаляется: разбираться с ней придётся
    после того, как машина снова работает.
    """
    raise NotImplementedError


def rollback(runtime: Path) -> SwapReport:
    """Ручной откат на ``.previous``. Обратим: повторный вызов вернёт обратно.

    БД он **не** откатывает — автоматический ``alembic downgrade`` через N
    ревизий небезопасен. Вызывающий обязан сказать это человеку.
    """
    raise NotImplementedError


def install_runtime(
    repo: Path,
    sha: str,
    *,
    runtime: Path,
    python_version: str = DEFAULT_PYTHON_VERSION,
) -> InstallStepReport:
    """Фаза A целиком: сборка → свап → проверка → при провале авто-откат."""
    raise NotImplementedError


def sync_path_tool(src: Path) -> str:
    """``uv tool install --force`` — догнать установку в PATH до собранной ревизии.

    Зовётся **только из дочернего процесса** (см. ``update_service``): этот шаг
    подменяет ``~/.local/share/uv/tools/cod-doc``, и если команду запустили из
    ``~/.local/bin/cod-doc``, это вторая самозамена в одном прогоне.
    """
    raise NotImplementedError


def installed_versions(*, runtime: Path, repo: Path | None) -> list[InstallVersion]:
    """Версии всех установок cod-doc: рантайм сервисов, PATH (uv tool), чекаут.

    «Какая у меня версия» — вопрос без единственного ответа, поэтому отвечаем
    сразу за все.
    """
    raise NotImplementedError


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float = SUBPROCESS_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Единственная точка запуска внешних бинарей — её и подменяют тесты."""
    raise NotImplementedError
