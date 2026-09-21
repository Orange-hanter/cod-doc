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

import contextlib
import importlib.metadata
import os
import shutil
import subprocess
import tempfile
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

#: Ветка по умолчанию, когда remote о ней молчит (свежий клон без origin/HEAD).
FALLBACK_DEFAULT_BRANCH = "origin/main"

#: Соседи рабочего каталога рантайма. Все четыре лежат рядом с ним намеренно:
#: ``mv`` между ними — rename на одной ФС, то есть мгновенный и атомарный.
SUFFIX_STAGED = ".staged"
SUFFIX_PREVIOUS = ".previous"
SUFFIX_BROKEN = ".broken"
SUFFIX_ROLLBACK_TMP = ".rollback-tmp"

#: Сколько символов sha показываем человеку в сообщениях.
SHA_DISPLAY_LEN = 12

#: Больше этого у shebang'а читать нечего, а файл может оказаться и бинарным.
SHEBANG_MAX_BYTES = 512

#: Ответ на «какая версия», когда установка есть, а версия не выясняется.
UNKNOWN_VERSION = "версия не определяется"

#: Имя дистрибутива, чьи метаданные спрашиваем про адрес репозитория.
DISTRIBUTION_NAME = "cod-doc"

#: Метки из `[project.urls]` в порядке доверия. Клонируем по первой найденной:
#: `Repository` — канонический ключ PyPI под исходники, остальные два стоят
#: следом на случай проекта, назвавшего ту же ссылку иначе.
_REPO_URL_LABELS = ("repository", "source", "homepage")

#: Чем чинить отсутствующий бинарь. Сообщение без подсказки заставляет человека
#: гуглить ровно в тот момент, когда у него не работают сервисы.
_MISSING_BINARY_HINTS = {
    "uv": "brew install uv",
    "git": "xcode-select --install",
}

#: Версия берётся импортом пакета, а не `cod-doc --version`: флаг появился
#: только в ADO-189, и опора на него означала бы умение собирать лишь ревизии
#: новее самого себя — то есть потерю отката на старый релиз.
_VERSION_PROBE = "import cod_doc; print(cod_doc.__version__)"
_VERSION_REPORT_PROBE = 'import cod_doc; print("cod-doc, version", cod_doc.__version__)'


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

    Адрес для клона: ``$COD_DOC_REPO_URL`` → ``[project.urls]`` установленного
    дистрибутива. Второе делает осмысленным апгрейд из голой установки, у
    которой чекаута нет вовсе: репозиторий публичный, и анонимный клон работает
    без кредов.

    Явно переданный ``repo`` без ``.git`` — ошибка, а не повод пойти дальше по
    списку: молча собрать из другого дерева хуже, чем отказаться.
    """
    if repo is not None:
        explicit = Path(repo).expanduser()
        if not (explicit / ".git").exists():
            raise RuntimeError_(f"не репозиторий: {explicit} (нет .git)")
        return explicit

    for candidate in _repo_candidates():
        if (candidate / ".git").exists():
            return candidate
    if (src_cache / ".git").exists():
        return src_cache

    url = os.environ.get("COD_DOC_REPO_URL", "").strip() or _repo_url_from_metadata()
    if not url:
        # Сюда приходим, только если дистрибутив не установлен
        # (`PackageNotFoundError`) или его колесо собрано до появления
        # [project.urls] — метаданные пишутся при сборке и задним числом не
        # обновляются. Спрашивать больше неоткуда, остаётся сказать прямо.
        raise RuntimeError_(
            "негде взять исходники cod-doc: нет локального чекаута "
            f"(ни --repo, ни $COD_DOC_REPO, ни ~/Git/_my/cod-doc, ни {src_cache}), "
            "а метаданные установки не называют репозиторий. "
            "Задай $COD_DOC_REPO_URL для клонирования, укажи чекаут через "
            "--repo или обнови без пересборки рантайма (--skip-install)."
        )
    src_cache.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", "--filter=blob:none", url, str(src_cache)])
    return src_cache


def default_branch(repo: Path) -> str:
    """Ветка по умолчанию у remote — через ``git rev-parse --abbrev-ref origin/HEAD``.

    Хардкод ``origin/main`` из bash-скрипта не переносим: у чужого чекаута
    remote может называться иначе. При неудаче — фолбэк на ``origin/main``.
    """
    try:
        proc = _run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "origin/HEAD"])
    except RuntimeError_:
        return FALLBACK_DEFAULT_BRANCH
    return proc.stdout.strip() or FALLBACK_DEFAULT_BRANCH


def resolve_ref(repo: Path, ref: str) -> str:
    """Развернуть ревизию в sha, отвергнув всё, что не влито в ветку по умолчанию.

    ``git fetch origin --tags --prune``, затем
    ``git merge-base --is-ancestor <sha> <default-branch>``: катится только
    смерженное. Локальная ветка, грязное дерево и незапушенный коммит физически
    не могут попасть в релиз.
    """
    _run(["git", "-C", str(repo), "fetch", "origin", "--tags", "--prune", "--quiet"])
    try:
        proc = _run(["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"])
    except RuntimeError_ as exc:
        raise RuntimeError_(f"не разрешается ревизия '{ref}': {exc}") from exc
    sha = proc.stdout.strip()
    if not sha:
        raise RuntimeError_(f"не разрешается ревизия '{ref}': git ничего не вернул")

    branch = default_branch(repo)
    ancestor = _run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, branch],
        check=False,
    )
    if ancestor.returncode != 0:
        raise RuntimeError_(
            f"ревизия {ref} ({sha[:SHA_DISPLAY_LEN]}) не является предком {branch} — "
            "катится только смерженное"
        )
    return sha


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

    Не бросает: неудача сборки — это отчёт ``ok=False`` с диагнозом, потому что
    ``src`` из отчёта всё равно нужен вызывающему для :func:`drop_build_src`.
    Человекочитаемый ``ref`` в сигнатуру не заведён, поэтому в ``BuildReport.ref``
    кладётся sha; вызывающий, который знает исходный ref, вправе его переписать.
    """
    staged = _sibling(runtime, SUFFIX_STAGED)
    src = Path(tempfile.mkdtemp(prefix="cod-doc-build-")) / "src"
    report = BuildReport(ref=sha, sha=sha, version="", src=str(src), staged=str(staged), ok=False)
    try:
        report.version = _build(
            repo,
            sha,
            src=src,
            staged=staged,
            python_version=_python_version(python_version),
        )
    except RuntimeError_ as exc:
        report.error = str(exc)
        return report
    report.ok = True
    return report


def _build(repo: Path, sha: str, *, src: Path, staged: Path, python_version: str) -> str:
    """Тело сборки: экспорт ревизии, venv, install, два smoke-теста. Отдаёт версию."""
    # Временный git-worktree, а не `git archive`: в нём лежит ровно содержимое
    # коммита (незакоммиченное физически не может уехать в релиз), но при этом
    # есть `.git` — и setuptools-scm выводит версию сам.
    _run(["git", "-C", str(repo), "worktree", "add", "--detach", "--quiet", str(src), sha])

    shutil.rmtree(staged, ignore_errors=True)
    _run(["uv", "venv", "--python", python_version, "--relocatable", str(staged), "--quiet"])
    _run(["uv", "pip", "install", "--python", str(staged / "bin" / "python"), str(src), "--quiet"])

    try:
        probe = _run([str(staged / "bin" / "python"), "-P", "-c", _VERSION_PROBE])
    except RuntimeError_ as exc:
        raise RuntimeError_(f"собранный рантайм не импортируется: {exc}") from exc
    version = probe.stdout.strip()
    if not version:
        raise RuntimeError_("собранный рантайм не назвал свою версию")

    try:
        _run([str(staged / "bin" / "cod-doc"), "--help"])
    except RuntimeError_ as exc:
        raise RuntimeError_(
            f"собранный рантайм не запускается: console-script cod-doc падает ({exc})"
        ) from exc
    return version


def drop_build_src(src: Path) -> None:
    """Снять временный worktree. Зовётся и из ``finally`` при провале сборки."""
    if src.exists():
        try:
            removed = _run(
                ["git", "-C", str(src), "worktree", "remove", "--force", str(src)],
                check=False,
            ).returncode
        except RuntimeError_:
            removed = 1
        if removed != 0:
            shutil.rmtree(src, ignore_errors=True)
    # Каталог от `mkdtemp` снимаем только если он опустел: rmdir на непустом
    # молча ничего не сделает, а сносить его рекурсивно нам нечего.
    with contextlib.suppress(OSError):
        src.parent.rmdir()


def swap(runtime: Path) -> SwapReport:
    """Атомарно подменить рантайм: ``.previous`` ← текущий, ``.staged`` → рабочий.

    ``mv`` внутри ``~/.cod-doc`` — это rename на одном ФС: иноды не меняются,
    живые демоны продолжают работать на старом дереве до ``kickstart``.
    """
    staged = _sibling(runtime, SUFFIX_STAGED)
    previous = _sibling(runtime, SUFFIX_PREVIOUS)
    if not staged.is_dir():
        return SwapReport(
            runtime=str(runtime),
            previous=None,
            ok=False,
            error=f"нечего ставить: нет собранного рантайма {staged}",
        )
    shutil.rmtree(previous, ignore_errors=True)
    had_previous = runtime.exists()
    if had_previous:
        runtime.rename(previous)
    staged.rename(runtime)
    return SwapReport(runtime=str(runtime), previous=str(previous) if had_previous else None)


def verify_swapped(runtime: Path) -> tuple[bool, str]:
    """Smoke-тест ПОСЛЕ свапа: ``bin/cod-doc-mcp --help``.

    Проверка до свапа не видит поломок самого переезда — прежде всего
    запечённого shebang'а.
    """
    binary = runtime / "bin" / "cod-doc-mcp"
    if not binary.exists():
        return False, f"в подменённом рантайме нет {binary}"
    try:
        proc = _run([str(binary), "--help"], check=False)
    except RuntimeError_ as exc:
        return False, str(exc)
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = lines[-1] if lines else f"код {proc.returncode}"
        return False, f"рантайм не работает по конечному пути: {tail}"
    return True, ""


def rollback_failed_swap(runtime: Path) -> SwapReport:
    """Аварийный откат: рабочий → ``.broken``, ``.previous`` → рабочий.

    Сломанная сборка сохраняется, а не удаляется: разбираться с ней придётся
    после того, как машина снова работает.
    """
    previous = _sibling(runtime, SUFFIX_PREVIOUS)
    broken = _sibling(runtime, SUFFIX_BROKEN)
    shutil.rmtree(broken, ignore_errors=True)
    if runtime.exists():
        runtime.rename(broken)
    if not previous.is_dir():
        # Первая установка: восстанавливать нечего. Сломанное дерево всё равно
        # уезжает в `.broken` — оставить его по рабочему пути значит отдать
        # launchd бинарь, который он будет перезапускать по кругу.
        return SwapReport(
            runtime=str(runtime),
            previous=None,
            broken=str(broken),
            rolled_back=False,
            ok=False,
            error=f"прежнего рантайма не было, восстанавливать нечего; сборка — {broken}",
        )
    previous.rename(runtime)
    return SwapReport(
        runtime=str(runtime),
        previous=None,
        broken=str(broken),
        rolled_back=True,
        ok=False,
        error=f"свап откачен, вернулся прежний рантайм; сломанная сборка — {broken}",
    )


def rollback(runtime: Path) -> SwapReport:
    """Ручной откат на ``.previous``. Обратим: повторный вызов вернёт обратно.

    БД он **не** откатывает — автоматический ``alembic downgrade`` через N
    ревизий небезопасен. Вызывающий обязан сказать это человеку.
    """
    previous = _sibling(runtime, SUFFIX_PREVIOUS)
    tmp = _sibling(runtime, SUFFIX_ROLLBACK_TMP)
    if not previous.is_dir():
        return SwapReport(
            runtime=str(runtime),
            previous=None,
            ok=False,
            error=f"нечего откатывать: нет {previous}",
        )
    shutil.rmtree(tmp, ignore_errors=True)
    had_current = runtime.exists()
    if had_current:
        runtime.rename(tmp)
    previous.rename(runtime)
    if had_current:
        tmp.rename(previous)
    return SwapReport(
        runtime=str(runtime),
        previous=str(previous) if had_current else None,
        rolled_back=True,
    )


def install_runtime(
    repo: Path,
    sha: str,
    *,
    runtime: Path,
    python_version: str = DEFAULT_PYTHON_VERSION,
) -> InstallStepReport:
    """Фаза A целиком: сборка → свап → проверка → при провале авто-откат."""
    build = build_staged(repo, sha, runtime=runtime, python_version=python_version)
    report = InstallStepReport(build=build)
    try:
        if not build.ok:
            report.error = build.error
            return report

        swapped = swap(runtime)
        report.swap = swapped
        if not swapped.ok:
            report.error = swapped.error
            return report

        ok, detail = verify_swapped(runtime)
        if ok:
            report.ok = True
        else:
            # Живые сервисы важнее новой версии: откатываемся без участия
            # человека и оставляем сломанную сборку для разбирательства.
            rolled = rollback_failed_swap(runtime)
            rolled.error = f"{detail}; {rolled.error}" if rolled.error else detail
            report.swap = rolled
            report.error = rolled.error
        report.versions = installed_versions(runtime=runtime, repo=repo)
        return report
    finally:
        drop_build_src(Path(build.src))


def sync_path_tool(src: Path) -> str:
    """``uv tool install --force`` — догнать установку в PATH до собранной ревизии.

    Зовётся **только из дочернего процесса** (см. ``update_service``): этот шаг
    подменяет ``~/.local/share/uv/tools/cod-doc``, и если команду запустили из
    ``~/.local/bin/cod-doc``, это вторая самозамена в одном прогоне.

    Возвращает версию, которую после этого называет ``~/.local/bin/cod-doc``,
    либо :data:`UNKNOWN_VERSION`.
    """
    _run(["uv", "tool", "install", "--force", "--from", str(src), "cod-doc", "--quiet"])
    return _probe_version(_path_tool_binary()) or UNKNOWN_VERSION


def installed_versions(*, runtime: Path, repo: Path | None) -> list[InstallVersion]:
    """Версии всех установок cod-doc: рантайм сервисов, PATH (uv tool), чекаут.

    «Какая у меня версия» — вопрос без единственного ответа, поэтому отвечаем
    сразу за все.
    """
    targets = [
        ("рантайм сервисов", runtime / "bin" / "cod-doc"),
        ("PATH (uv tool)", _path_tool_binary()),
    ]
    if repo is not None:
        targets.append(("репозиторий (editable)", repo / ".venv" / "bin" / "cod-doc"))
    return [_installed_version(name, binary) for name, binary in targets]


def _installed_version(name: str, binary: Path) -> InstallVersion:
    """Одна строка отчёта о версиях: сначала `--version`, потом импорт."""
    if not binary.exists():
        return InstallVersion(
            name=name,
            binary=str(binary),
            version=None,
            error=f"не установлен ({binary})",
        )
    version = _probe_version(binary)
    return InstallVersion(
        name=name,
        binary=str(binary),
        version=version,
        error=None if version else UNKNOWN_VERSION,
    )


def _probe_version(binary: Path) -> str | None:
    """`--version`, а при неудаче — импорт пакета интерпретатором этой установки.

    `--version` появился в ADO-189: на установке старше него строка про старую
    сборку — самая важная в выводе — иначе была бы пустой ровно тогда, когда
    она и нужна.
    """
    reported = _capture(binary, "--version")
    if reported:
        return reported
    for interpreter in _interpreter_candidates(binary):
        # `-P` обязателен: иначе cwd попадает в sys.path и `import cod_doc`
        # берёт локальное рабочее дерево вместо установленного пакета.
        imported = _capture(interpreter, "-P", "-c", _VERSION_REPORT_PROBE)
        if imported:
            return imported
    return None


def _capture(binary: Path, *args: str) -> str | None:
    """stdout удачного запуска или ``None`` — ошибки здесь ожидаемы и не фатальны."""
    try:
        proc = _run([str(binary), *args], check=False)
    except RuntimeError_:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _interpreter_candidates(binary: Path) -> list[Path]:
    """Чем пробовать импортировать пакет, если console-script не знает `--version`.

    Первый кандидат — интерпретатор из shebang'а. Второй — ``python`` рядом с
    РЕАЛЬНЫМ путём бинаря: у uv-tool сам бинарь симлинк в чужое дерево, и
    «рядом» надо считать от цели; а у venv, собранного с ``--relocatable``,
    shebang вообще ведёт в ``/bin/sh`` (обёртка ищет интерпретатор в рантайме),
    так что сосед — единственный рабочий вариант.
    """
    candidates: list[Path] = []
    shebang = _shebang_interpreter(binary)
    if shebang is not None:
        candidates.append(shebang)
    sibling = binary.resolve().parent / "python"
    if sibling not in candidates:
        candidates.append(sibling)
    return [path for path in candidates if path.exists()]


def _shebang_interpreter(binary: Path) -> Path | None:
    """Путь из первой строки ``#!...``; ``#!/usr/bin/env python3`` — второе слово."""
    try:
        with binary.open("rb") as handle:
            first = handle.readline(SHEBANG_MAX_BYTES)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    parts = first[2:].decode("utf-8", errors="replace").strip().split()
    if not parts:
        return None
    if parts[0].endswith("env"):
        rest = parts[1:]
        return Path(rest[0]) if rest else None
    return Path(parts[0])


def _repo_url_from_metadata() -> str | None:
    """Адрес репозитория из ``[project.urls]`` установленного дистрибутива.

    ``importlib.metadata`` — stdlib, границу свапа не нарушает. Каждый
    ``Project-URL`` — строка вида ``Repository, https://…``, поэтому метку и
    ссылку приходится разбирать самим.

    ``None`` — если пакет не установлен (запуск из чекаута без install) или его
    колесо собрано до появления секции: метаданные пишутся при сборке.
    """
    try:
        entries = importlib.metadata.metadata(DISTRIBUTION_NAME).get_all("Project-URL") or []
    except importlib.metadata.PackageNotFoundError:
        return None
    urls: dict[str, str] = {}
    for entry in entries:
        label, _, url = str(entry).partition(",")
        if url.strip():
            urls.setdefault(label.strip().lower(), url.strip())
    return next((urls[label] for label in _REPO_URL_LABELS if label in urls), None)


def _repo_candidates() -> list[Path]:
    """Где искать чекаут, когда его не передали явно."""
    candidates: list[Path] = []
    env_repo = os.environ.get("COD_DOC_REPO", "").strip()
    if env_repo:
        candidates.append(Path(env_repo).expanduser())
    candidates.append(Path.home() / "Git" / "_my" / "cod-doc")
    return candidates


def _path_tool_binary() -> Path:
    """Установка в PATH — ``~/.local/bin/cod-doc`` (симлинк uv-tool)."""
    return Path.home() / ".local" / "bin" / "cod-doc"


def _sibling(runtime: Path, suffix: str) -> Path:
    """Сосед рантайма по суффиксу: ``runtime`` → ``runtime.staged`` и т.п."""
    return runtime.with_name(runtime.name + suffix)


def _python_version(explicit: str) -> str:
    """Явный аргумент сильнее окружения; ``$COD_DOC_PYTHON`` — только для дефолта."""
    if explicit != DEFAULT_PYTHON_VERSION:
        return explicit
    return os.environ.get("COD_DOC_PYTHON", "").strip() or DEFAULT_PYTHON_VERSION


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float = SUBPROCESS_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Единственная точка запуска внешних бинарей — её и подменяют тесты."""
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=check,
            cwd=str(cwd) if cwd is not None else None,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        hint = _MISSING_BINARY_HINTS.get(Path(argv[0]).name)
        suffix = f" ({hint})" if hint else ""
        raise RuntimeError_(f"не найден `{argv[0]}`{suffix}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError_(f"`{' '.join(argv)}` не ответила за {timeout:.0f} с") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or f"код {exc.returncode}"
        raise RuntimeError_(f"`{' '.join(argv)}` завершилась с ошибкой: {detail}") from exc
