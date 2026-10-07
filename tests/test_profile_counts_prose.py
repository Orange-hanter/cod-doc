"""RFC 26 §8.1 / ADO-207 / DEBT-001: числа тулов MCP в прозе = живому каталогу.

До DEBT-001 число тулов каждого профиля повторялось руками в 53 местах
8 файлов, а этот тест только падал — чинил агент, открывая файлы по одному.
Теперь числа стоят в двух местах (докстринг ``profiles.py`` и
``docs/mcp-integration.md``), а сверку и починку делает
``cod_doc.mcp.profile_counts``: здесь его ``--check``-логика, в pre-commit —
``--write`` через ``scripts/regen.sh``.

Ожидание берётся из кода (свежий каталог + ``keep_tool``), а не из другой
копии прозы: сверка двух копий поймала бы только их расхождение между собой.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from cod_doc.mcp import profile_counts
from cod_doc.mcp.profile_counts import PROSE_COUNTERS, catalog, live_counts, plan, run

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def names() -> frozenset[str]:
    # Одна сборка каталога на модуль: ~150 тулов, ~0.5 с.
    return catalog()


def test_repo_prose_matches_live_catalog(names: frozenset[str]) -> None:
    result = plan(names=names)
    assert not result.problems, "\n".join(result.problems)
    assert not result.changed, (
        f"числа разошлись с каталогом в {sorted(result.changed)} — "
        f"{profile_counts.WRITE_COMMAND} (pre-commit делает это сам)"
    )


def test_every_counter_has_one_capture_group() -> None:
    """Автоисправитель заменяет ровно группу 1 — вторая группа сломала бы замену."""
    bad = [p for _, p, _ in PROSE_COUNTERS if re.compile(p).groups != 1]
    assert not bad, f"регулярки не с одной группой: {bad}"


def test_live_counts_partition_the_catalog(names: frozenset[str]) -> None:
    counts = live_counts(names)
    assert counts["full"] == len(names)
    assert counts["agent"] < counts["minimal"] < counts["standard"] < counts["full"]


# ── Автоисправитель на временном дереве ──────────────────────────────────

_NAMES = frozenset({"doc_get", "doc_list", "task_get", "agent_capabilities"})


def _tree(root: Path, *, family_rows: str, itogo: int, full_claim: int) -> None:
    """Минимальное дерево с обоими файлами из PROSE_COUNTERS."""
    (root / "cod_doc" / "mcp").mkdir(parents=True)
    (root / "docs").mkdir()
    profiles_text = (
        "``agent`` (RFC 25 §3.2/§3.5, **default**) — 9 curator tools\n"
        "``minimal`` — 9-tool\n``standard`` — 9-tool\n``full`` — all 9 tools\n"
        "# Agent — 9 curator tools\n"
    )
    (root / "cod_doc" / "mcp" / "profiles.py").write_text(profiles_text, encoding="utf-8")
    doc = (
        "Agent profile — 9-tool\n"
        f"MCP server с **{full_claim} инструментами**\n"
        "cod-doc-mcp  # agent (default) — 9 curator\n"
        "--profile minimal  # 9 cold-start\n--profile standard  # 9 CRUD\n"
        "--profile full  # все 9\n| `standard` | 9 |\n| `agent` | 9 |\n"
        "Профиль сервера по умолчанию — `agent` (9 curator-тулов\n\n"
        "| Семейство | Кол-во | Назначение | Ключевые тулы |\n"
        "|---|---:|---|---|\n"
        f"{family_rows}"
        f"| **ИТОГО** | **{itogo}** | | |\n"
    )
    (root / "docs" / "mcp-integration.md").write_text(doc, encoding="utf-8")


def test_write_fixes_every_number(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (
        "| **doc.\\*** | 7 | docs, see `task_get` here | `doc_get`, `doc_list` |\n"
        "| **task.\\*** | 0 | tasks | `task_get`, `agent_capabilities` |\n"
    )
    _tree(tmp_path, family_rows=rows, itogo=1, full_claim=2)
    monkeypatch.setattr(profile_counts, "catalog", lambda: _NAMES)

    assert run(["--check"], root=tmp_path) == 1
    assert run(["--write"], root=tmp_path) == 0
    assert run(["--check"], root=tmp_path) == 0

    doc = (tmp_path / "docs" / "mcp-integration.md").read_text(encoding="utf-8")
    # Число строки — тулы последней колонки; упоминание в «Назначении» не считается.
    assert "| **doc.\\*** | 2 |" in doc
    assert "| **task.\\*** | 2 |" in doc
    assert "| **ИТОГО** | **4** |" in doc
    assert "**4 инструментами**" in doc
    profiles = (tmp_path / "cod_doc" / "mcp" / "profiles.py").read_text(encoding="utf-8")
    assert "— 4 curator tools" not in profiles  # agent ≠ full
    assert "``full`` — all 4 tools" in profiles


def test_unlisted_tool_is_named_not_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Семейство для нового тула выбирает человек: автомат называет тул и падает."""
    rows = "| **doc.\\*** | 2 | docs | `doc_get`, `doc_list`, `doc_gone` |\n"
    _tree(tmp_path, family_rows=rows, itogo=4, full_claim=4)
    monkeypatch.setattr(profile_counts, "catalog", lambda: _NAMES)

    assert run(["--write"], root=tmp_path) == 1

    err = capsys.readouterr().err
    assert "`task_get` не перечислен" in err
    assert "`agent_capabilities` не перечислен" in err
    assert "`doc_gone`, которого нет в каталоге" in err


def test_reworded_prose_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Регулярка, переставшая матчить, — выключенная проверка; молчать нельзя."""
    rows = "| **all** | 4 | x | `doc_get`, `doc_list`, `task_get`, `agent_capabilities` |\n"
    _tree(tmp_path, family_rows=rows, itogo=4, full_claim=4)
    profiles = tmp_path / "cod_doc" / "mcp" / "profiles.py"
    profiles.write_text(
        profiles.read_text(encoding="utf-8").replace("``minimal`` — 9-tool", "minimal: nine"),
        encoding="utf-8",
    )
    monkeypatch.setattr(profile_counts, "catalog", lambda: _NAMES)

    assert run(["--check"], root=tmp_path) == 1
    assert "``minimal`` — (\\d+)-tool" in capsys.readouterr().err
