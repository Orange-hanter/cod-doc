"""RFC 26 §8.1 / ADO-207: счётчики MCP-профилей в прозе = живому каталогу.

Число тулов каждого профиля (agent/minimal/standard/full) повторено руками
примерно в десяти файлах. Машинно проверялись только два —
``EXPECTED_PROFILE_COUNTS`` в ``tests/test_server_profiles.py`` и итог
``docs/mcp-integration.md`` в ``tests/test_mcp_integration_doc.py``;
остальные восемь разъезжались молча: MASTER.md обещал 126 тулов при
реальных 146, README — 143/147 после того, как каталог уже вырос.

Ожидание берётся из живого каталога — свежий FastMCP с теми же модулями
тулов, что регистрирует ``cod_doc.mcp.server``, затем ``apply_profile``.
Сверять прозу с прозой (докстрингом ``profiles.py``, другим документом или
``EXPECTED_PROFILE_COUNTS``) гейт не вправе: он поймал бы только
расхождение двух копий, а не расхождение копий с кодом.

Добавил или убрал тул — правь прозу, на которую гейт покажет красным.
Появился новый файл, называющий число тулов профиля, — добавь строку в
``PROSE_COUNTERS``. Исторические числа с датой в прозу не пишутся, а не
исключаются из гейта.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.mcp import server as mcp_server

ROOT = Path(__file__).resolve().parents[1]

# (путь от корня репо, регулярка с одной группой-числом, профиль)
PROSE_COUNTERS: list[tuple[str, str, str]] = [
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
    # cod_doc/mcp/server.py — help опции --profile
    ("cod_doc/mcp/server.py", r"agent=(\d+) curator tools", "agent"),
    ("cod_doc/mcp/server.py", r"minimal=(\d+) cold-start", "minimal"),
    ("cod_doc/mcp/server.py", r"standard=(\d+) DB-backed", "standard"),
    ("cod_doc/mcp/server.py", r"full=(\d+) including legacy", "full"),
    # AGENTS.md
    ("AGENTS.md", r"Поверхность `agent` — (\d+)", "agent"),
    ("AGENTS.md", r"Счётчики (\d+)/\d+/\d+/\d+", "agent"),
    ("AGENTS.md", r"Счётчики \d+/(\d+)/\d+/\d+", "minimal"),
    ("AGENTS.md", r"Счётчики \d+/\d+/(\d+)/\d+", "standard"),
    ("AGENTS.md", r"Счётчики \d+/\d+/\d+/(\d+)", "full"),
    ("AGENTS.md", r"--profile minimal\s+# (\d+)-tool", "minimal"),
    ("AGENTS.md", r"--profile full\s+# все (\d+)", "full"),
    ("AGENTS.md", r"``agent`` — \*\*default\*\*: (\d+) curator-тулов", "agent"),
    ("AGENTS.md", r"``minimal`` — (\d+)-tool", "minimal"),
    ("AGENTS.md", r"``standard`` — (\d+) DB-backed", "standard"),
    ("AGENTS.md", r"``full`` — все (\d+) тулов", "full"),
    # CLAUDE.md — раздел «MCP: один файл = одна семья тулов»
    ("CLAUDE.md", r"\*\*дефолтный\*\*, (\d+) curator-тулов", "agent"),
    ("CLAUDE.md", r"`minimal` (\d+)\s*/\s*`standard`", "minimal"),
    ("CLAUDE.md", r"`standard` (\d+)\s*/\s*`full`", "standard"),
    ("CLAUDE.md", r"/\s*`full` (\d+)\.", "full"),
    # CLAUDE.md — таблица anti-drift
    ("CLAUDE.md", r"counts профилей \((\d+)/\d+/\d+/\d+\)", "agent"),
    ("CLAUDE.md", r"counts профилей \(\d+/(\d+)/\d+/\d+\)", "minimal"),
    ("CLAUDE.md", r"counts профилей \(\d+/\d+/(\d+)/\d+\)", "standard"),
    ("CLAUDE.md", r"counts профилей \(\d+/\d+/\d+/(\d+)\)", "full"),
    # CLAUDE.md — раздел «Инструментарий сессии»
    ("CLAUDE.md", r"профиль `standard`, (\d+) тулов", "standard"),
    ("CLAUDE.md", r"профиль `agent`, (\d+) curator-тулов", "agent"),
    # docs/mcp-integration.md
    ("docs/mcp-integration.md", r"Agent profile — (\d+)-tool", "agent"),
    ("docs/mcp-integration.md", r"MCP server с \*\*(\d+) инструментами\*\*", "full"),
    ("docs/mcp-integration.md", r"cod-doc-mcp\s+# agent \(default\) — (\d+) curator", "agent"),
    ("docs/mcp-integration.md", r"--profile minimal\s+# (\d+) cold-start", "minimal"),
    ("docs/mcp-integration.md", r"--profile standard\s+# (\d+) CRUD", "standard"),
    ("docs/mcp-integration.md", r"--profile full\s+# все (\d+)", "full"),
    ("docs/mcp-integration.md", r"\| `standard` \| (\d+) \|", "standard"),
    ("docs/mcp-integration.md", r"\| `agent` \| (\d+) \|", "agent"),
    (
        "docs/mcp-integration.md",
        r"Профиль сервера по умолчанию — `agent` \((\d+) curator-тулов",
        "agent",
    ),
    ("docs/mcp-integration.md", r"\*\*ИТОГО\*\* \| \*\*(\d+)\*\*", "full"),
    # deploy/launchd/README.md
    ("deploy/launchd/README.md", r"`standard`, (\d+) тулов", "standard"),
    ("deploy/launchd/README.md", r"CUR-007/008/016\) (\d+) тулов", "agent"),
    # README.md
    ("README.md", r"`agent` MCP profile is a (\d+)-tool", "agent"),
    ("README.md", r"no (\d+)-tool cold start", "full"),
    ("README.md", r"`agent` \((\d+) curator tools", "agent"),
    ("README.md", r"`minimal` \((\d+)\)", "minimal"),
    ("README.md", r"`standard` \((\d+)\)", "standard"),
    ("README.md", r"`full` \((\d+)\)", "full"),
    # MASTER.md — строка статуса
    ("MASTER.md", r"(\d+) MCP-тула \(профили", "full"),
    ("MASTER.md", r"профили agent/minimal/standard/full — (\d+)/\d+/\d+/\d+", "agent"),
    ("MASTER.md", r"профили agent/minimal/standard/full — \d+/(\d+)/\d+/\d+", "minimal"),
    ("MASTER.md", r"профили agent/minimal/standard/full — \d+/\d+/(\d+)/\d+", "standard"),
    ("MASTER.md", r"профили agent/minimal/standard/full — \d+/\d+/\d+/(\d+)", "full"),
]

PROFILES = ("agent", "minimal", "standard", "full")


def _live_count(profile: str, monkeypatch: pytest.MonkeyPatch) -> int:
    """Число тулов профиля на свежем сервере, собранном из модулей server.py.

    Модульный ``mcp_server.mcp`` могли уже обрезать другие тесты процесса,
    поэтому каталог собирается заново: тот же набор модулей тулов, что
    импортирует ``cod_doc.mcp.server``, затем ``apply_profile`` на нём.
    """
    fresh = FastMCP("COD-DOC", json_response=True)
    for value in vars(mcp_server).values():
        if (
            inspect.ismodule(value)
            and value.__name__.startswith("cod_doc.mcp.tools.")
            and hasattr(value, "register")
        ):
            value.register(fresh)
    monkeypatch.setattr(mcp_server, "mcp", fresh)
    monkeypatch.setattr(mcp_server, "_ACTIVE_PROFILE", mcp_server._ACTIVE_PROFILE)
    monkeypatch.setenv("COD_DOC_ACTIVE_PROFILE", "full")
    mcp_server.apply_profile(profile)
    return len(fresh._tool_manager._tools)


@pytest.fixture(scope="module")
def live_counts() -> dict[str, int]:
    # Четыре сборки каталога по ~150 тулов — одна на модуль, а не на кейс:
    # в function-scope setup стоил ~0.5 с на каждый параметризованный кейс.
    counts = {}
    for profile in PROFILES:
        with pytest.MonkeyPatch.context() as mp:
            counts[profile] = _live_count(profile, mp)
    return counts


@pytest.mark.parametrize(("path", "pattern", "profile"), PROSE_COUNTERS)
def test_every_prose_counter_matches_live_catalog(
    path: str, pattern: str, profile: str, live_counts: dict[str, int]
) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    found = [int(m) for m in re.findall(pattern, text)]
    wrong = [n for n in found if n != live_counts[profile]]
    assert not wrong, (
        f"{path}: /{pattern}/ называет {wrong}, а живой профиль {profile} = {live_counts[profile]}"
    )


@pytest.mark.parametrize(("path", "pattern", "profile"), PROSE_COUNTERS)
def test_every_pattern_still_matches(path: str, pattern: str, profile: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    assert re.search(pattern, text), (
        f"{path}: /{pattern}/ ({profile}) не нашла ни одного вхождения — "
        "прозу переформулировали, поправь регулярку, иначе проверка молча выключена"
    )


def test_live_counts_literal(live_counts: dict[str, int]) -> None:
    assert live_counts == {"agent": 6, "minimal": 21, "standard": 166, "full": 170}
