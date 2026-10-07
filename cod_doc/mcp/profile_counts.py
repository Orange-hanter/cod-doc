"""DEBT-001: числа тулов MCP в документации — производные, а не ручные.

    python -m cod_doc.mcp.profile_counts            # живые счётчики профилей
    python -m cod_doc.mcp.profile_counts --check    # exit 1 + что разошлось
    python -m cod_doc.mcp.profile_counts --write    # переписать; печатает изменённые файлы

Счётчики профилей (agent/minimal/standard/full) правились руками в 8 файлах,
53 вхождения — 26–42 коммита с июня только ради чисел. Теперь число стоит в
двух местах (докстринг ``profiles.py`` и ``docs/mcp-integration.md``), а
остальная проза отсылает сюда. ``--write`` зовёт ``scripts/regen.sh`` из
pre-commit, так что агенту число трогать не нужно вовсе.

Ожидание берётся из кода, а не из другой копии прозы: свежий каталог из тех
же модулей тулов, что регистрирует ``cod_doc.mcp.server``, отфильтрованный
``profiles.keep_tool``. ``apply_profile`` не годится — он режет общий ``mcp``
на месте.

Таблица семейств в каталоге: число в строке — количество тулов, перечисленных
в её последней колонке, ИТОГО — размер каталога. Тул, не попавший ни в одну
строку, автомат не раскладывает: семейство выбирает человек, и ``--check`` /
``--write`` называют такой тул по имени.
"""

from __future__ import annotations

import argparse
import inspect
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cod_doc.mcp.profiles import keep_tool

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("agent", "minimal", "standard", "full")
CATALOG_DOC = "docs/mcp-integration.md"
WRITE_COMMAND = "python -m cod_doc.mcp.profile_counts --write"

_EXIT_DRIFT = 1

# (путь от корня репо, регулярка с ровно одной группой-числом, профиль).
# Новое место, называющее число тулов профиля, — строка сюда, иначе оно
# протухнет молча. Лучше же не называть число вовсе, а сослаться на модуль.
PROSE_COUNTERS: tuple[tuple[str, str, str], ...] = (
    # cod_doc/mcp/profiles.py — модульный докстринг
    (
        "cod_doc/mcp/profiles.py",
        r"``agent`` \(RFC 25 §3\.2/§3\.5, \*\*default\*\*\) — (\d+) curator tools",
        "agent",
    ),
    ("cod_doc/mcp/profiles.py", r"``minimal`` — (\d+)-tool", "minimal"),
    ("cod_doc/mcp/profiles.py", r"``standard`` — (\d+)-tool", "standard"),
    ("cod_doc/mcp/profiles.py", r"``full`` — all (\d+) tools", "full"),
    # cod_doc/mcp/profiles.py — комментарий над AGENT_TOOLS
    ("cod_doc/mcp/profiles.py", r"# Agent — (\d+) curator tools", "agent"),
    # docs/mcp-integration.md
    (CATALOG_DOC, r"Agent profile — (\d+)-tool", "agent"),
    (CATALOG_DOC, r"MCP server с \*\*(\d+) инструментами\*\*", "full"),
    (CATALOG_DOC, r"cod-doc-mcp\s+# agent \(default\) — (\d+) curator", "agent"),
    (CATALOG_DOC, r"--profile minimal\s+# (\d+) cold-start", "minimal"),
    (CATALOG_DOC, r"--profile standard\s+# (\d+) CRUD", "standard"),
    (CATALOG_DOC, r"--profile full\s+# все (\d+)", "full"),
    (CATALOG_DOC, r"\| `standard` \| (\d+) \|", "standard"),
    (CATALOG_DOC, r"\| `agent` \| (\d+) \|", "agent"),
    (CATALOG_DOC, r"Профиль сервера по умолчанию — `agent` \((\d+) curator-тулов", "agent"),
    (CATALOG_DOC, r"\*\*ИТОГО\*\* \| \*\*(\d+)\*\*", "full"),
)

_FAMILY_HEADER = "| Семейство | Кол-во |"
_FAMILY_ROW = re.compile(
    r"^(?P<head>\|\s*\*\*(?!ИТОГО)[^|]+?\*\*\s*\|\s*)(?P<n>\d+)(?P<tail>\s*\|.*)$"
)
_TOOL_NAME = re.compile(r"`([a-z][a-z0-9_]*)`")


def catalog() -> frozenset[str]:
    """Имена всех тулов, что регистрирует сервер, — на свежем FastMCP.

    Модульный ``server.mcp`` в процессе тестов уже мог быть обрезан
    ``apply_profile``, поэтому каталог собирается заново из тех же модулей.
    Импорт сервера тяжёлый и живёт в теле: модуль импортируют тесты.
    """
    from mcp.server.fastmcp import FastMCP

    from cod_doc.mcp import server

    # По имени модуля, а не по атрибуту: переменная цикла регистрации в
    # server.py (`_module`) указывает на последний модуль ещё раз, и повторный
    # register() шумит «Tool already exists».
    modules = {
        value.__name__: value
        for value in vars(server).values()
        if inspect.ismodule(value)
        and value.__name__.startswith("cod_doc.mcp.tools.")
        and hasattr(value, "register")
    }
    fresh = FastMCP("COD-DOC", json_response=True)
    for module in modules.values():
        module.register(fresh)
    return frozenset(fresh._tool_manager._tools)


def live_counts(names: frozenset[str]) -> dict[str, int]:
    """Число тулов каждого профиля."""
    return {p: sum(keep_tool(n, p) for n in names) for p in PROFILES}


@dataclass
class Plan:
    """Что надо переписать и что автомат починить не может."""

    #: путь от корня → новый текст файла (только изменившиеся файлы)
    changed: dict[str, str] = field(default_factory=dict)
    #: расхождения, которые решает человек
    problems: list[str] = field(default_factory=list)


def _fix_prose(path: str, text: str, counts: dict[str, int], problems: list[str]) -> str:
    for entry_path, pattern, profile in PROSE_COUNTERS:
        if entry_path != path:
            continue
        matches = list(re.finditer(pattern, text))
        if not matches:
            problems.append(
                f"{path}: /{pattern}/ ({profile}) не нашла вхождений — прозу "
                "переформулировали, поправь PROSE_COUNTERS в cod_doc/mcp/profile_counts.py"
            )
        for m in reversed(matches):
            text = text[: m.start(1)] + str(counts[profile]) + text[m.end(1) :]
    return text


def _fix_family_table(text: str, names: frozenset[str], problems: list[str]) -> str:
    """Пересчитать строки таблицы семейств по перечисленным в них тулам."""
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith(_FAMILY_HEADER)), None)
    if start is None:
        problems.append(f"{CATALOG_DOC}: не найдена таблица «{_FAMILY_HEADER} …»")
        return text

    owners: dict[str, list[str]] = {}
    for i in range(start + 2, len(lines)):
        line = lines[i]
        if not line.startswith("|"):
            break
        m = _FAMILY_ROW.match(line)
        if m is None:
            continue
        family = line.split("|")[1].strip()
        tools_cell = line.rsplit("|", 2)[-2]
        listed = set(_TOOL_NAME.findall(tools_cell))
        problems.extend(
            f"{CATALOG_DOC}: в строке {family} указан `{name}`, которого нет в каталоге"
            for name in sorted(listed - names)
        )
        for name in listed & names:
            owners.setdefault(name, []).append(family)
        lines[i] = m["head"] + str(len(listed & names)) + m["tail"]

    problems.extend(
        f"{CATALOG_DOC}: тул `{name}` не перечислен ни в одной строке таблицы семейств — "
        "впиши его в колонку «Ключевые тулы» своего семейства"
        for name in sorted(names - owners.keys())
    )
    problems.extend(
        f"{CATALOG_DOC}: тул `{name}` перечислен в нескольких семействах: {', '.join(families)}"
        for name, families in sorted(owners.items())
        if len(families) > 1
    )
    return "\n".join(lines)


def plan(root: Path = ROOT, names: frozenset[str] | None = None) -> Plan:
    """Собрать правки, не трогая диск."""
    names = catalog() if names is None else names
    counts = live_counts(names)
    result = Plan()
    for path in dict.fromkeys(p for p, _, _ in PROSE_COUNTERS):
        original = (root / path).read_text(encoding="utf-8")
        text = _fix_prose(path, original, counts, result.problems)
        if path == CATALOG_DOC:
            text = _fix_family_table(text, names, result.problems)
        if text != original:
            result.changed[path] = text
    return result


def run(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog="python -m cod_doc.mcp.profile_counts")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1, если числа разошлись")
    mode.add_argument("--write", action="store_true", help="переписать числа в файлах")
    args = parser.parse_args(argv)

    if not (args.check or args.write):
        for profile, n in live_counts(catalog()).items():
            print(f"{profile}\t{n}")
        return 0

    result = plan(root)
    for problem in result.problems:
        print(problem, file=sys.stderr)

    if args.write:
        for path, text in result.changed.items():
            (root / path).write_text(text, encoding="utf-8")
            print(path)
    else:
        for path in result.changed:
            print(f"{path}: числа разошлись с каталогом — {WRITE_COMMAND}", file=sys.stderr)
        if result.changed:
            return _EXIT_DRIFT
    return _EXIT_DRIFT if result.problems else 0


if __name__ == "__main__":
    sys.exit(run())
