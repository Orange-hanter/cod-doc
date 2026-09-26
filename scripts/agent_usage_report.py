#!/usr/bin/env python3
"""AFT-016 / RFC 27 §5 — метрика встраивания cod-doc по транскриптам харнесса.

Скрипт сканирует верхнеуровневые ``*.jsonl`` сессий Claude Code в
``--project-dir`` и считает, как агент работает с cod-doc:

* вызовы MCP-тулов (``tool_use`` с именем ``mcp__cod-doc__*``) — счётчики,
  ошибки ``is_error`` и размеры ``tool_result`` по каждому тулу;
* вызовы CLI (``tool_use`` ``Bash`` с командой ``cod-doc <группа>``);
* прямой SQL в ``state.db`` (``tool_use`` ``Bash``, где команда содержит
  ``state.db`` и ``sqlite3`` или ``python``), раскладка по 8 категориям;
* сессии: всего в окне и хотя бы с одним MCP-вызовом.

Категоризация прямого SQL (b2s9): сначала ``write`` (INSERT INTO /
UPDATE .. SET / DELETE FROM), затем ``schema`` (sqlite_master, .schema,
.tables, PRAGMA table_info) — обе без учёта регистра SQL-ключевых слов.
Дальше извлекаются таблицы после FROM/JOIN, и категория выбирается по
итоговому порядку приоритета:

    task > doc_section > revision_activity > plan > link

Порядок оставлен стартовым: ручной прогон на живом каталоге с
``--until 2026-09-23`` (шаг 3 b2s9) дал отклонение от baseline только
из-за удалённых харнессом транскриптов (27 сессий в окне против 40),
а не из-за перекрёстного приоритета категорий — оснований для
перестановки нет.

Только стандартная библиотека: читает транскрипты харнесса, а не БД,
и обязан работать без venv. В CI не запускается; тесты вызывают его
подпроцессом. Повторный замер после доставки секций A–F делается
``--since <дата доставки>`` и сравнивается с baseline.

Baseline 2026-09-23 (40 сессий): MCP 398 вызовов в 20/40 сессиях,
CLI ~900, прямой SQL 407 (task 155, doc/section 113, other 53, schema 37,
revision/activity 36, plan 5, write 5, link 3); ошибки ``task_create``
11/52; медиана/максимум ответа ``ctx_drift`` 15.9 КБ, ``plan_ready`` 15.1 КБ.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

#: Каталог транскриптов Claude Code этого репозитория по умолчанию.
DEFAULT_PROJECT_DIR = "~/.claude/projects/-Users-dakh-Git--my-cod-doc"

#: Префикс MCP-тулов cod-doc в транскриптах харнесса.
MCP_PREFIX = "mcp__cod-doc__"

#: Верхнеуровневые группы CLI `cod-doc` (литеральный кортеж RFC 27 §5).
CLI_GROUPS = (
    "adapter",
    "adr",
    "agent",
    "audit",
    "completion",
    "ctx",
    "doc",
    "embed",
    "finding",
    "hash",
    "hub",
    "import",
    "ingest",
    "link",
    "mcp",
    "obligation",
    "plan",
    "project",
    "reindex",
    "revision",
    "routine",
    "runtime",
    "scenario",
    "search",
    "serve",
    "story",
    "structure",
    "task",
    "tui",
    "update",
    "upgrade",
    "wizard",
)

#: `cod-doc <группа>` в начале команды или после разделителя shell,
#: с необязательным префиксом пути (`.venv/bin/cod-doc task list`).
#: `cod-doc-mcp`, `cd /x/cod-doc && pytest` и `.cod-doc/state.db`
#: не матчатся: после `cod-doc` обязаны быть пробел и группа CLI.
_CLI_RE = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?cod-doc\s+(" + "|".join(CLI_GROUPS) + r")\b")

#: Все 8 категорий прямого SQL в порядке проверки (литеральный набор b2s9).
SQL_CATEGORIES = (
    "write",
    "schema",
    "task",
    "doc_section",
    "revision_activity",
    "plan",
    "link",
    "other",
)

#: Пишущие операторы побеждают любые табличные категории.
_SQL_WRITE_RE = re.compile(
    r"\bINSERT\s+INTO\b|\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b", re.IGNORECASE
)

#: Интроспекция схемы — вторая по приоритету.
_SQL_SCHEMA_RE = re.compile(
    r"sqlite_master|\.schema\b|\.tables\b|PRAGMA\s+table_info", re.IGNORECASE
)

#: Имя таблицы после FROM/JOIN; кавычки и бэктики вокруг имени снимаются.
_SQL_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+[\"'`]?(\w+)[\"'`]?", re.IGNORECASE)

#: Точные имена табличных категорий в итоговом порядке приоритета.
_SQL_TABLE_NAMES: tuple[tuple[str, frozenset[str]], ...] = (
    ("task", frozenset({"task", "task_dependency", "affected_file", "ready_tasks"})),
    ("doc_section", frozenset({"document", "section", "doc_node"})),
    ("revision_activity", frozenset({"revision", "activity_event"})),
    ("plan", frozenset({"plan", "plan_section"})),
    ("link", frozenset({"link"})),
)

#: Несуществующий --project-dir — ошибка использования, как у argparse.
EXIT_USAGE = 2


def _direct_sql_category(command: str) -> str | None:
    """Категория прямого SQL-вызова или None, если вызов не прямой SQL.

    Прямой SQL — команда содержит ``state.db`` и клиент ``sqlite3``/``python``
    (регистр важен); write и schema проверяются первыми, затем таблицы
    после FROM/JOIN по приоритету из ``_SQL_TABLE_NAMES``.
    """
    if "state.db" not in command or ("sqlite3" not in command and "python" not in command):
        return None
    if _SQL_WRITE_RE.search(command):
        return "write"
    if _SQL_SCHEMA_RE.search(command):
        return "schema"
    tables = {name.lower() for name in _SQL_TABLE_RE.findall(command)}
    for category, names in _SQL_TABLE_NAMES:
        if tables & names or (category == "task" and any(t.startswith("task_") for t in tables)):
            return category
    return "other"


def _utc_date(record: dict[str, Any]) -> date | None:
    """UTC-дата поля ``timestamp`` записи; None, если поля нет или оно битое."""
    ts = record.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        moment = datetime.fromisoformat(ts[:-1] + "+00:00" if ts.endswith("Z") else ts)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


def _result_size(content: object) -> int:
    """Размер ``tool_result`` в байтах UTF-8: строка целиком или сумма text-блоков."""
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    if isinstance(content, list):
        return sum(
            len(block["text"].encode("utf-8"))
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return 0


def _in_window(day: date, since: date | None, until: date | None) -> bool:
    return (since is None or day >= since) and (until is None or day <= until)


class _ToolStat:
    """Счётчики одного MCP-тула."""

    def __init__(self) -> None:
        self.calls = 0
        self.errors = 0
        self.sizes: list[int] = []


class _Report:
    """Аккумулятор отчёта по всем сессиям окна."""

    def __init__(self) -> None:
        self.sessions_total = 0
        self.sessions_with_mcp = 0
        self.mcp_total = 0
        self.mcp_by_tool: dict[str, _ToolStat] = {}
        self.cli_total = 0
        self.cli_by_group: Counter[str] = Counter()
        self.sql_total = 0
        self.sql_by_category: Counter[str] = Counter()


def _collect_record(
    record: dict[str, Any],
    tool_uses: dict[str, dict[str, Any]],
    results: list[tuple[str, int, bool]],
) -> None:
    """Взять из записи tool_use / tool_result блоки, если они там есть."""
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    record_type = record.get("type")
    for block in content:
        if not isinstance(block, dict):
            continue
        if record_type == "assistant" and block.get("type") == "tool_use":
            tool_id = block.get("id")
            name = block.get("name")
            if isinstance(tool_id, str) and isinstance(name, str) and tool_id not in tool_uses:
                tool_uses[tool_id] = {"name": name, "input": block.get("input")}
        elif record_type == "user" and block.get("type") == "tool_result":
            tool_id = block.get("tool_use_id")
            if isinstance(tool_id, str):
                results.append(
                    (tool_id, _result_size(block.get("content")), block.get("is_error") is True)
                )


def _merge_session(
    tool_uses: dict[str, dict[str, Any]],
    results: list[tuple[str, int, bool]],
    report: _Report,
) -> None:
    """Слить собранные из сессии вызовы и результаты в общий отчёт."""
    mcp_ids: dict[str, str] = {}
    session_has_mcp = False
    for tool_id, use in tool_uses.items():
        name = use["name"]
        if name.startswith(MCP_PREFIX):
            short = name[len(MCP_PREFIX) :]
            mcp_ids[tool_id] = short
            stat = report.mcp_by_tool.setdefault(short, _ToolStat())
            stat.calls += 1
            report.mcp_total += 1
            session_has_mcp = True
        elif name == "Bash":
            command = use["input"].get("command") if isinstance(use["input"], dict) else None
            if isinstance(command, str):
                groups = set(_CLI_RE.findall(command))
                if groups:
                    report.cli_total += 1
                    for group in groups:
                        report.cli_by_group[group] += 1
                sql_category = _direct_sql_category(command)
                if sql_category is not None:
                    report.sql_total += 1
                    report.sql_by_category[sql_category] += 1
    if session_has_mcp:
        report.sessions_with_mcp += 1

    for tool_id, size, is_error in results:
        short = mcp_ids.get(tool_id)
        if short is None:
            continue
        stat = report.mcp_by_tool[short]
        stat.sizes.append(size)
        if is_error:
            stat.errors += 1


def _scan_session(path: Path, since: date | None, until: date | None, report: _Report) -> None:
    """Разобрать один файл-сессию и слить счётчики в ``report``."""
    tool_uses: dict[str, dict[str, Any]] = {}
    results: list[tuple[str, int, bool]] = []
    has_records_in_window = False
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            day = _utc_date(record)
            if day is None or not _in_window(day, since, until):
                continue
            has_records_in_window = True
            _collect_record(record, tool_uses, results)
    if not has_records_in_window:
        return
    report.sessions_total += 1
    _merge_session(tool_uses, results, report)


def _build_json(report: _Report, since: str | None, until: str | None) -> dict[str, Any]:
    by_tool: dict[str, Any] = {}
    for name, stat in report.mcp_by_tool.items():
        by_tool[name] = {
            "calls": stat.calls,
            "errors": stat.errors,
            "result_bytes_median": round(statistics.median(stat.sizes)) if stat.sizes else None,
            "result_bytes_max": max(stat.sizes) if stat.sizes else None,
        }
    by_tool = dict(sorted(by_tool.items(), key=lambda item: (-item[1]["calls"], item[0])))
    by_group = dict(sorted(report.cli_by_group.items(), key=lambda item: (-item[1], item[0])))
    by_category = {category: report.sql_by_category[category] for category in SQL_CATEGORIES}
    return {
        "window": {"since": since, "until": until},
        "sessions": {"total": report.sessions_total, "with_mcp": report.sessions_with_mcp},
        "mcp": {"total": report.mcp_total, "by_tool": by_tool},
        "cli": {"total": report.cli_total, "by_group": by_group},
        "direct_sql": {"total": report.sql_total, "by_category": by_category},
    }


def _print_human(data: dict[str, Any]) -> None:
    """Короткая человекочитаемая таблица; формат тестами не фиксируется."""
    sessions = data["sessions"]
    print(f"Sessions: {sessions['total']} (with MCP: {sessions['with_mcp']})")
    print(f"MCP calls: {data['mcp']['total']}")
    print(f"  {'tool':<32} {'calls':>6} {'errors':>6} {'median KB':>10} {'max KB':>10}")
    for name, stat in data["mcp"]["by_tool"].items():
        median = stat["result_bytes_median"]
        maximum = stat["result_bytes_max"]
        median_kb = f"{median / 1024:.1f}" if median is not None else "-"
        max_kb = f"{maximum / 1024:.1f}" if maximum is not None else "-"
        print(f"  {name:<32} {stat['calls']:>6} {stat['errors']:>6} {median_kb:>10} {max_kb:>10}")
    print(f"CLI calls: {data['cli']['total']}")
    for group, count in data["cli"]["by_group"].items():
        print(f"  {group:<32} {count:>6}")
    print(f"Direct SQL calls: {data['direct_sql']['total']}")
    for category, count in data["direct_sql"]["by_category"].items():
        print(f"  {category:<32} {count:>6}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path(DEFAULT_PROJECT_DIR).expanduser(),
        help="каталог сессий Claude Code с *.jsonl (default: %(default)s)",
    )
    parser.add_argument(
        "--since", default=None, help="нижняя граница UTC-даты, YYYY-MM-DD, включительно"
    )
    parser.add_argument(
        "--until", default=None, help="верхняя граница UTC-даты, YYYY-MM-DD, включительно"
    )
    parser.add_argument("--json", action="store_true", help="машинный JSON вместо таблицы")
    args = parser.parse_args(argv)

    project_dir: Path = args.project_dir.expanduser()
    if not project_dir.is_dir():
        print(f"error: project dir not found: {project_dir}", file=sys.stderr)
        return EXIT_USAGE
    try:
        since = date.fromisoformat(args.since) if args.since else None
        until = date.fromisoformat(args.until) if args.until else None
    except ValueError as exc:
        print(f"error: bad date: {exc}", file=sys.stderr)
        return EXIT_USAGE

    report = _Report()
    for path in sorted(project_dir.glob("*.jsonl")):
        if path.is_file():
            _scan_session(path, since, until, report)

    data = _build_json(report, args.since, args.until)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_human(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
