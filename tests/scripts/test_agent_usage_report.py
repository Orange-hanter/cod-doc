"""AFT-016: тесты ``scripts/agent_usage_report.py`` (RFC 27 §5).

Скрипт вызывается подпроцессом от корня репозитория и разбирает stdout
(``--json``). Эталоны — литеральные числа, выведенные из фикстур вручную;
импорт функций скрипта запрещён, чтобы тест проверял контракт, а не
совпадение реализации с самой собой.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = "scripts/agent_usage_report.py"

#: Модули, которые скрипту разрешено импортировать (только stdlib).
_STDLIB_ALLOWLIST = {
    "__future__",
    "argparse",
    "collections",
    "datetime",
    "json",
    "pathlib",
    "re",
    "statistics",
    "sys",
    "typing",
}


def _assistant(ts: str, blocks: list[dict]) -> dict:
    return {"type": "assistant", "timestamp": ts, "message": {"content": blocks}}


def _user(ts: str, blocks: list[dict]) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"content": blocks}}


def _tool_use(tool_id: str, name: str, command: str | None = None) -> dict:
    block: dict = {"type": "tool_use", "id": tool_id, "name": name, "input": {}}
    if command is not None:
        block["input"] = {"command": command}
    return block


def _tool_result(tool_id: str, content: object, is_error: bool = False) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "is_error": is_error,
        "content": content,
    }


def _write_session(path: Path, records: list) -> None:
    """Пишет синтетический jsonl: dict'ы сериализуются, строки — как есть."""
    lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(project_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT, "--project-dir", str(project_dir), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_json(project_dir: Path, *args: str) -> dict:
    proc = _run(project_dir, "--json", *args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_mcp_counts_errors_and_sizes(tmp_path: Path) -> None:
    """2 сессии: task_create 4 вызова / 1 ошибка, размеры ctx_drift и plan_ready."""
    _write_session(
        tmp_path / "s1.jsonl",
        [
            # task_create ×3: результаты 10/20/30 байт, один is_error
            _assistant("2026-09-23T10:00:00Z", [_tool_use("tc1", "mcp__cod-doc__task_create")]),
            _user("2026-09-23T10:00:01Z", [_tool_result("tc1", "x" * 10)]),
            _assistant("2026-09-23T10:01:00Z", [_tool_use("tc2", "mcp__cod-doc__task_create")]),
            _user("2026-09-23T10:01:01Z", [_tool_result("tc2", "x" * 20, is_error=True)]),
            _assistant("2026-09-23T10:02:00Z", [_tool_use("tc3", "mcp__cod-doc__task_create")]),
            _user("2026-09-23T10:02:01Z", [_tool_result("tc3", "x" * 30)]),
            # ctx_drift ×2: строки 100 и 300 байт
            _assistant("2026-09-23T10:03:00Z", [_tool_use("cd1", "mcp__cod-doc__ctx_drift")]),
            _user("2026-09-23T10:03:01Z", [_tool_result("cd1", "x" * 100)]),
            _assistant("2026-09-23T10:04:00Z", [_tool_use("cd2", "mcp__cod-doc__ctx_drift")]),
            _user("2026-09-23T10:04:01Z", [_tool_result("cd2", "x" * 300)]),
            # plan_ready ×1: список text-блоков суммарно 50 байт
            _assistant("2026-09-23T10:05:00Z", [_tool_use("pr1", "mcp__cod-doc__plan_ready")]),
            _user(
                "2026-09-23T10:05:01Z",
                [_tool_result("pr1", [{"text": "x" * 30}, {"text": "y" * 20}])],
            ),
        ],
    )
    _write_session(
        tmp_path / "s2.jsonl",
        [
            _assistant("2026-09-23T11:00:00Z", [_tool_use("tc4", "mcp__cod-doc__task_create")]),
            _user("2026-09-23T11:00:01Z", [_tool_result("tc4", "x" * 40)]),
            _assistant("2026-09-23T11:01:00Z", [_tool_use("cd3", "mcp__cod-doc__ctx_drift")]),
            _user("2026-09-23T11:01:01Z", [_tool_result("cd3", "x" * 1000)]),
        ],
    )

    data = _run_json(tmp_path)

    assert data["mcp"]["total"] == 8
    assert data["mcp"]["by_tool"]["task_create"] == {
        "calls": 4,
        "errors": 1,
        "result_bytes_median": 25,
        "result_bytes_max": 40,
    }
    ctx_drift = data["mcp"]["by_tool"]["ctx_drift"]
    assert ctx_drift["result_bytes_median"] == 300
    assert ctx_drift["result_bytes_max"] == 1000
    assert data["mcp"]["by_tool"]["plan_ready"]["result_bytes_max"] == 50
    assert data["sessions"] == {"total": 2, "with_mcp": 2}


def test_duplicate_tool_use_id_counted_once(tmp_path: Path) -> None:
    """Повторная строка с тем же tool_use id не считается дважды."""
    use = _assistant("2026-09-23T10:00:00Z", [_tool_use("dup1", "mcp__cod-doc__task_create")])
    _write_session(tmp_path / "s1.jsonl", [use, dict(use)])

    data = _run_json(tmp_path)

    assert data["mcp"]["by_tool"]["task_create"]["calls"] == 1
    assert data["mcp"]["total"] == 1


def test_cli_detection(tmp_path: Path) -> None:
    """Группы CLI распознаются; cod-doc-mcp, cd-путь и state.db — нет."""
    _write_session(
        tmp_path / "s1.jsonl",
        [
            _assistant(
                "2026-09-23T10:00:00Z",
                [_tool_use("b1", "Bash", ".venv/bin/cod-doc task list")],
            ),
            _assistant(
                "2026-09-23T10:01:00Z",
                [_tool_use("b2", "Bash", "cod-doc task show X && cod-doc plan ready y")],
            ),
            _assistant(
                "2026-09-23T10:02:00Z",
                [_tool_use("b3", "Bash", "cod-doc-mcp --profile agent")],
            ),
            _assistant(
                "2026-09-23T10:03:00Z",
                [_tool_use("b4", "Bash", "cd /Users/x/_my/cod-doc && pytest")],
            ),
            _assistant(
                "2026-09-23T10:04:00Z",
                [_tool_use("b5", "Bash", "sqlite3 .cod-doc/state.db 'select 1'")],
            ),
        ],
    )

    data = _run_json(tmp_path)

    assert data["cli"]["total"] == 2
    assert data["cli"]["by_group"] == {"task": 2, "plan": 1}
    assert data["mcp"]["total"] == 0


def test_window_filters_by_utc_date(tmp_path: Path) -> None:
    """--since/--until включительно по UTC-дате; пустая в окне сессия не считается."""
    _write_session(
        tmp_path / "s1.jsonl",
        [
            _assistant("2026-09-22T23:59:59Z", [_tool_use("w1", "mcp__cod-doc__ctx_drift")]),
            _assistant("2026-09-23T00:00:00Z", [_tool_use("w2", "mcp__cod-doc__ctx_drift")]),
            _assistant("2026-09-23T23:59:59Z", [_tool_use("w3", "mcp__cod-doc__ctx_drift")]),
            _assistant("2026-09-24T00:00:00Z", [_tool_use("w4", "mcp__cod-doc__ctx_drift")]),
        ],
    )
    _write_session(
        tmp_path / "s2.jsonl",
        [_assistant("2026-09-22T12:00:00Z", [_tool_use("w5", "mcp__cod-doc__ctx_drift")])],
    )

    data = _run_json(tmp_path, "--since", "2026-09-23", "--until", "2026-09-23")

    assert data["window"] == {"since": "2026-09-23", "until": "2026-09-23"}
    assert data["mcp"]["total"] == 2
    assert data["sessions"] == {"total": 1, "with_mcp": 1}


def test_sessions_with_mcp(tmp_path: Path) -> None:
    """with_mcp — только сессии хотя бы с одним MCP-вызовом."""
    mcp_call = _assistant("2026-09-23T10:00:00Z", [_tool_use("m1", "mcp__cod-doc__ctx_drift")])
    plain = _assistant("2026-09-23T10:00:00Z", [_tool_use("b1", "Bash", "ls -la")])
    _write_session(tmp_path / "s1.jsonl", [mcp_call])
    _write_session(tmp_path / "s2.jsonl", [mcp_call])
    _write_session(tmp_path / "s3.jsonl", [plain])

    data = _run_json(tmp_path)

    assert data["sessions"] == {"total": 3, "with_mcp": 2}


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    """Битая JSON-строка и запись без message молча пропускаются, код 0."""
    _write_session(
        tmp_path / "s1.jsonl",
        [
            '{"type": "assistant", "timestamp": "2026-09-23T10:00:00Z", "message": ',
            {"type": "assistant", "timestamp": "2026-09-23T10:00:01Z"},
            _assistant("2026-09-23T10:00:02Z", [_tool_use("ok1", "mcp__cod-doc__ctx_drift")]),
        ],
    )

    data = _run_json(tmp_path)

    assert data["mcp"]["total"] == 1
    assert data["sessions"]["total"] == 1


def test_direct_sql_categories(tmp_path: Path) -> None:
    """8 Bash-вызовов прямого SQL — ровно по одному в каждую категорию."""
    commands = [
        "sqlite3 .cod-doc/state.db \"SELECT * FROM task WHERE status = 'todo'\"",
        "python -c \"import sqlite3; c=sqlite3.connect('state.db'); "
        "print(c.execute('SELECT * FROM section JOIN document ON 1=1').fetchall())\"",
        "sqlite3 state.db 'SELECT * FROM activity_event'",
        "sqlite3 state.db 'SELECT * FROM plan_section'",
        "sqlite3 state.db 'SELECT * FROM link'",
        "sqlite3 state.db '.schema task'",
        "sqlite3 state.db \"UPDATE task SET status='done'\"",
        "sqlite3 state.db 'SELECT * FROM finding'",
    ]
    records = [
        _assistant(f"2026-09-23T10:{i:02d}:00Z", [_tool_use(f"sql{i}", "Bash", cmd)])
        for i, cmd in enumerate(commands)
    ]
    _write_session(tmp_path / "s1.jsonl", records)

    data = _run_json(tmp_path)

    assert data["direct_sql"]["total"] == 8
    assert data["direct_sql"]["by_category"] == {
        "task": 1,
        "doc_section": 1,
        "revision_activity": 1,
        "plan": 1,
        "link": 1,
        "schema": 1,
        "write": 1,
        "other": 1,
    }


def test_direct_sql_requires_state_db_and_client(tmp_path: Path) -> None:
    """Без state.db или без sqlite3/python в команде вызов не прямой SQL."""
    _write_session(
        tmp_path / "s1.jsonl",
        [
            _assistant(
                "2026-09-23T10:00:00Z",
                [_tool_use("n1", "Bash", "sqlite3 other.db 'SELECT * FROM task'")],
            ),
            _assistant(
                "2026-09-23T10:01:00Z",
                [_tool_use("n2", "Bash", "cat .cod-doc/state.db")],
            ),
        ],
    )

    data = _run_json(tmp_path)

    assert data["direct_sql"]["total"] == 0
    assert data["direct_sql"]["by_category"] == {
        "write": 0,
        "schema": 0,
        "task": 0,
        "doc_section": 0,
        "revision_activity": 0,
        "plan": 0,
        "link": 0,
        "other": 0,
    }


def test_write_wins_over_tables(tmp_path: Path) -> None:
    """DELETE FROM section — категория write, хотя section из doc_section."""
    _write_session(
        tmp_path / "s1.jsonl",
        [
            _assistant(
                "2026-09-23T10:00:00Z",
                [_tool_use("w1", "Bash", 'sqlite3 state.db "DELETE FROM section WHERE id = 1"')],
            ),
        ],
    )

    data = _run_json(tmp_path)

    assert data["direct_sql"]["total"] == 1
    assert data["direct_sql"]["by_category"] == {
        "write": 1,
        "schema": 0,
        "task": 0,
        "doc_section": 0,
        "revision_activity": 0,
        "plan": 0,
        "link": 0,
        "other": 0,
    }


def test_missing_project_dir_exits_2(tmp_path: Path) -> None:
    proc = _run(tmp_path / "no-such-dir", "--json")

    assert proc.returncode == 2
    assert proc.stderr


def test_human_output_exits_0(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "s1.jsonl",
        [_assistant("2026-09-23T10:00:00Z", [_tool_use("h1", "mcp__cod-doc__ctx_drift")])],
    )

    proc = _run(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()


def test_script_is_stdlib_only() -> None:
    """AST-скан: ни одного импорта вне литерального allowlist stdlib."""
    tree = ast.parse((REPO_ROOT / SCRIPT).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    assert imported <= _STDLIB_ALLOWLIST
    assert "cod_doc" not in imported
    assert "sqlalchemy" not in imported
