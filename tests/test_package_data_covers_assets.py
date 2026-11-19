"""Анти-drift: каждый не-Python файл пакета попадает в колесо.

`[tool.setuptools.package-data]` перечисляет расширения поимённо, а не
«всё, что лежит рядом». Значит, файл нового типа добавляется в репозиторий
молча и так же молча исчезает из любой non-editable установки: в рабочем
дереве он есть, в `pip install .` — нет, и разница видна только в проде.

Так уехали два файла. `static/favicon.svg` не грузился ни в одном
контейнере и ни у одного из трёх демонов на машине — в списке стояли
`static/*.css` и `static/*.js`, `.svg` не было. `infra/migrations/script.py.mako`
не ставился вообще, из-за чего установленный `cod-doc` умел
`alembic upgrade`, но не `alembic revision`.

Editable-инстал это скрывает: он подставляет путь к рабочему дереву, где
файл на месте. Поэтому дефект и не ловился ни тестами, ни локальным
запуском — только сборкой.
"""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "cod_doc"

#: Каталоги, которых в дистрибутиве быть не должно.
_IGNORED_DIRS = {"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}

#: Файлы, которые намеренно не едут в колесо. Пустой: пока таких нет.
#: Появится — впиши сюда с обоснованием, а не расширяй `package-data`.
_INTENTIONALLY_UNSHIPPED: frozenset[str] = frozenset()


def _package_data_patterns() -> list[str]:
    """Globs из `[tool.setuptools.package-data]."cod_doc"`."""
    raw = (PROJECT_ROOT / "pyproject.toml").read_bytes()
    config = tomllib.loads(raw.decode("utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["cod_doc"]
    assert isinstance(patterns, list)
    return [str(p) for p in patterns]


def _asset_files() -> list[str]:
    """Пути не-Python файлов пакета относительно `cod_doc/`."""
    out: list[str] = []
    for path in PACKAGE_ROOT.rglob("*"):
        if not path.is_file() or path.suffix == ".py":
            continue
        if _IGNORED_DIRS & set(path.relative_to(PACKAGE_ROOT).parts):
            continue
        out.append(path.relative_to(PACKAGE_ROOT).as_posix())
    return sorted(out)


def _matches(rel_path: str, pattern: str) -> bool:
    """Glob с семантикой setuptools: `*` — внутри одного сегмента, `**` — на любой глубине.

    Раньше здесь стоял `fnmatch`, у которого `*` проходит сквозь `/`:
    `templates/web/*.html` «покрывал» `templates/web/_ui/ledger.html`, а
    setuptools этот файл в колесо не клал. Тест был зелёным, пока список
    вопросов, историй и документов на проде падал с `TemplateNotFound`
    (2026-11-19). `PurePosixPath.full_match` (3.13+) различает `*` и `**`
    так же, как setuptools.
    """
    return PurePosixPath(rel_path).full_match(pattern)


def test_every_asset_is_covered_by_package_data() -> None:
    patterns = _package_data_patterns()
    uncovered = [
        rel
        for rel in _asset_files()
        if rel not in _INTENTIONALLY_UNSHIPPED
        and not any(_matches(rel, pattern) for pattern in patterns)
    ]
    assert not uncovered, (
        "эти файлы не попадут в non-editable установку — добавь glob в "
        "[tool.setuptools.package-data] или внеси в _INTENTIONALLY_UNSHIPPED "
        f"с обоснованием: {uncovered}"
    )


def test_known_regressions_stay_covered() -> None:
    """Конкретные файлы, на которых дефект и был пойман.

    Отдельным кейсом, потому что общий тест выше пройдёт и на пустом
    наборе ассетов — например, если кто-то сузит `_asset_files`.
    `_ui/ledger.html` — кит Ledger, вложенный каталог шаблонов (2026-11-19).
    """
    patterns = _package_data_patterns()
    for rel in (
        "static/favicon.svg",
        "infra/migrations/script.py.mako",
        "templates/web/_ui/ledger.html",
    ):
        assert (PACKAGE_ROOT / rel).exists(), f"{rel} исчез из репозитория — тест устарел"
        assert any(_matches(rel, p) for p in patterns), f"{rel} снова не попадает в колесо"
