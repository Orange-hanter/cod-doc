"""AFT-014 (RFC 27 F15): прямой sqlite3 в скриптах плагина — только с обоснованием.

Скрипты `plugins/cod-doc/scripts/*.sh` исполняются хуками SessionStart и
PostToolUse, поэтому им оставлено прямое чтение `state.db`: CLI стоит на
два порядка дороже. Цена исключения — маркер `# sqlite3-direct:` над первым
вызовом, который объясняет причину и называет штатный путь для агентов и
людей, и `-readonly` на каждом вызове. Без маркера следующий читатель
скопирует sqlite3 в инструкцию для агента, откуда F15 его как раз вычищал.
"""

from __future__ import annotations

from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "plugins" / "cod-doc" / "scripts"
MIN_SCRIPTS = 3
MARKER = "# sqlite3-direct:"
MARKER_REQUIRED = ("хук", "cod-doc project list --json")
READONLY = "-readonly"


def _scripts() -> list[Path]:
    return sorted(SCRIPTS_DIR.glob("*.sh"))


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _call_lines(lines: list[str]) -> list[int]:
    """Индексы строк, где sqlite3 вызывается, а не упомянут или проверяется."""
    return [
        i
        for i, line in enumerate(lines)
        if "sqlite3 " in line and not _is_comment(line) and "command -v sqlite3" not in line
    ]


def _marker_text(lines: list[str], start: int) -> str:
    """Маркер вместе со следующими за ним строками-комментариями."""
    parts = [lines[start]]
    for line in lines[start + 1 :]:
        if not _is_comment(line):
            break
        parts.append(line)
    return "\n".join(parts)


def test_scripts_glob_not_empty() -> None:
    assert len(_scripts()) >= MIN_SCRIPTS


def test_every_sqlite_script_has_marker_above_first_call() -> None:
    checked = 0
    for script in _scripts():
        lines = script.read_text(encoding="utf-8").splitlines()
        calls = _call_lines(lines)
        if not calls:
            continue
        checked += 1
        markers = [i for i, line in enumerate(lines) if _is_comment(line) and MARKER in line]
        assert markers, f"{script.name}: вызов sqlite3 без комментария {MARKER!r}"
        assert markers[0] < calls[0], (
            f"{script.name}: {MARKER!r} (строка {markers[0] + 1}) стоит ниже "
            f"первого вызова sqlite3 (строка {calls[0] + 1})"
        )
        text = _marker_text(lines, markers[0])
        for needle in MARKER_REQUIRED:
            assert needle in text, f"{script.name}: в маркере нет {needle!r}"
    assert checked > 0, "ни в одном скрипте нет вызова sqlite3 — проверка ничего не стерегла"


def test_every_sqlite_call_is_readonly() -> None:
    for script in _scripts():
        lines = script.read_text(encoding="utf-8").splitlines()
        for i in _call_lines(lines):
            assert READONLY in lines[i], f"{script.name}:{i + 1}: sqlite3 без {READONLY}"
