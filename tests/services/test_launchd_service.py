"""ADO-192: launchd_service — рендер plist'ов и операции над сервисами.

`launchctl` и `urllib` живыми не зовутся: модуль держит по одной точке входа
(`_run_launchctl`, `_http_ok`), тесты подменяют их — как это сделано для `gh`
в `gh_service`.

Главный тест здесь — сверка `render_plist` с выводом bash-`render()` из
`deploy/launchd/cod-doc-services.sh`. Скрипт остаётся на машине и правится
руками; молчаливый разъезд порта или профиля между ним и Python-портом стоил
бы перезапуска всех харнессов машины.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cod_doc.services import launchd_service as launchd

REPO_ROOT = Path(__file__).resolve().parents[2]
BASH_SCRIPT = REPO_ROOT / "deploy" / "launchd" / "cod-doc-services.sh"

SPECS = {spec.label: spec for spec in launchd.SERVICES}
MCP_LABEL = "com.cod-doc.mcp"
AGENT_LABEL = "com.cod-doc.mcp-agent"
WEB_LABEL = "com.cod-doc.web"

API_PORT = 8765


# ── инфраструктура тестов ───────────────────────────────────────────────────
class FakeLaunchctl:
    """Подмена `_run_launchctl`: помнит вызовы и множество загруженных лейблов."""

    def __init__(self, *, loaded: set[str] | None = None, fail: set[str] | None = None):
        self.calls: list[list[str]] = []
        self.loaded: set[str] = set(loaded or ())
        self.fail: set[str] = set(fail or ())
        #: сколько раз `print` ещё должен соврать «загружен» (эмуляция
        #: асинхронного bootout).
        self.sticky: dict[str, int] = {}

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        verb = args[0]
        handler = getattr(self, f"_verb_{verb}", None)
        rc = 0 if handler is None else handler(args)
        return subprocess.CompletedProcess(["launchctl", *args], rc, "", "" if rc == 0 else "boom")

    @staticmethod
    def _label_of(target: str) -> str:
        return target.rsplit("/", 1)[-1]

    def _verb_print(self, args: list[str]) -> int:
        label = self._label_of(args[-1])
        left = self.sticky.get(label, 0)
        if left:
            self.sticky[label] = left - 1
            return 0
        return 0 if label in self.loaded else 1

    def _verb_bootout(self, args: list[str]) -> int:
        self.loaded.discard(self._label_of(args[-1]))
        return 0

    def _verb_bootstrap(self, args: list[str]) -> int:
        label = Path(args[-1]).name.removesuffix(".plist")
        if label in self.fail:
            return 1
        self.loaded.add(label)
        return 0

    def _verb_kickstart(self, args: list[str]) -> int:
        return 1 if self._label_of(args[-1]) in self.fail else 0

    def verbs(self, label: str) -> list[str]:
        """Какие глаголы `launchctl` касались этого лейбла, по порядку."""
        return [c[0] for c in self.calls if label in " ".join(c)]


class ProbeRecorder:
    """Подмена `_http_ok`: записывает Request'ы и отдаёт заданный вердикт."""

    def __init__(self, *, ok: bool = True):
        self.requests: list[urllib.request.Request] = []
        self.ok = ok

    def __call__(self, request: urllib.request.Request) -> bool:
        self.requests.append(request)
        return self.ok


@pytest.fixture
def launchctl(monkeypatch):
    """Поддерживаемая платформа + фальшивый `launchctl` + мгновенный sleep."""
    fake = FakeLaunchctl()
    monkeypatch.setattr(launchd, "is_supported", lambda: True)
    monkeypatch.setattr(launchd, "_run_launchctl", fake)
    monkeypatch.setattr(launchd.time, "sleep", lambda _seconds: None)
    return fake


@pytest.fixture
def probe(monkeypatch):
    recorder = ProbeRecorder()
    monkeypatch.setattr(launchd, "_http_ok", recorder)
    return recorder


@pytest.fixture
def layout(tmp_path):
    """Пути install'а: рантайм с «собранным» cod-doc-mcp и три каталога."""
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "cod-doc-mcp").write_text("#!/bin/sh\n", encoding="utf-8")
    return {
        "runtime": runtime,
        "workdir": tmp_path / ".cod-doc",
        "logs": tmp_path / "Logs",
        "agents": tmp_path / "LaunchAgents",
    }


def render(label: str, tmp_path: Path) -> str:
    """`render_plist` по раскладке, совпадающей с bash-скриптом при HOME=tmp."""
    return launchd.render_plist(
        SPECS[label],
        runtime=tmp_path / ".cod-doc" / "runtime",
        workdir=tmp_path / ".cod-doc",
        logs=tmp_path / "Library" / "Logs",
    )


def bash_rendered(home: Path) -> dict[str, str]:
    """Прогнать `cod-doc-services.sh render` и разобрать вывод по лейблам."""
    if not BASH_SCRIPT.is_file():
        pytest.skip(f"нет {BASH_SCRIPT} — рендер не с чем сверять")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("нет bash")
    proc = subprocess.run(
        [bash, str(BASH_SCRIPT), "render"],
        capture_output=True,
        text=True,
        check=True,
        env={"HOME": str(home), "PATH": os.environ.get("PATH", "")},
    )
    out: dict[str, list[str]] = {}
    current = ""
    for line in proc.stdout.splitlines():
        if line.startswith("=== "):
            current = Path(line[4:]).name.removesuffix(".plist")
            out[current] = []
            continue
        out[current].append(line)
    return {label: "\n".join(lines) + "\n" for label, lines in out.items()}


# ── рендер plist ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("label", [MCP_LABEL, AGENT_LABEL, WEB_LABEL])
def test_render_plist_matches_bash_render(label, tmp_path):
    """Снапшот: порт, профиль и аргументы не уезжают от bash-скрипта молча."""
    assert render(label, tmp_path) == bash_rendered(tmp_path)[label]


def test_working_directory_is_cod_doc_home(tmp_path):
    plist = render(MCP_LABEL, tmp_path)
    expected = f"  <key>WorkingDirectory</key>\n  <string>{tmp_path / '.cod-doc'}</string>"
    assert expected in plist


@pytest.mark.parametrize(
    "fragment",
    [
        "<key>RunAtLoad</key><true/>",
        "<key>KeepAlive</key><true/>",
        "<key>ThrottleInterval</key><integer>10</integer>",
    ],
)
def test_keepalive_block_present(fragment, tmp_path):
    assert fragment in render(WEB_LABEL, tmp_path)


def test_logs_go_to_one_file_per_label(tmp_path):
    plist = render(AGENT_LABEL, tmp_path)
    log = tmp_path / "Library" / "Logs" / f"{AGENT_LABEL}.log"
    assert f"<key>StandardOutPath</key><string>{log}</string>" in plist
    assert f"<key>StandardErrorPath</key><string>{log}</string>" in plist


def test_environment_variables_carry_homebrew_path(tmp_path):
    plist = render(MCP_LABEL, tmp_path)
    assert "<string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>" in plist
    assert "<key>PYTHONUNBUFFERED</key>\n    <string>1</string>" in plist


@pytest.mark.parametrize(
    ("label", "profile", "port"),
    [(MCP_LABEL, "standard", "8801"), (AGENT_LABEL, "agent", "8802")],
)
def test_mcp_plist_carries_profile_and_port(label, profile, port, tmp_path):
    plist = render(label, tmp_path)
    assert f"<string>--profile</string>\n    <string>{profile}</string>" in plist
    assert f"<string>--port</string>\n    <string>{port}</string>" in plist
    assert str(tmp_path / ".cod-doc" / "runtime" / "bin" / "cod-doc-mcp") in plist


def test_web_plist_has_no_port_flag(tmp_path):
    """Порт веба живёт в config.yaml; флаг в plist развёл бы их молча."""
    plist = render(WEB_LABEL, tmp_path)
    args = plist.split("<key>ProgramArguments</key>")[1].split("</array>")[0]
    assert "--port" not in args
    assert "<string>serve</string>" in args
    assert str(tmp_path / ".cod-doc" / "runtime" / "bin" / "cod-doc") in args


# ── bootout ─────────────────────────────────────────────────────────────────
def test_bootout_and_wait_polls_until_label_disappears(launchctl):
    launchctl.loaded.add(MCP_LABEL)
    launchctl.sticky[MCP_LABEL] = 3  # bootout асинхронен: print ещё врёт

    assert launchd.bootout_and_wait(MCP_LABEL) is True
    assert launchctl.verbs(MCP_LABEL) == ["bootout", "print", "print", "print", "print"]


def test_bootout_and_wait_gives_up_without_raising(launchctl):
    """Лейбл не отпустили за бюджет — False, а не исключение."""
    launchctl.loaded.add(MCP_LABEL)
    launchctl.sticky[MCP_LABEL] = 10**6

    assert launchd.bootout_and_wait(MCP_LABEL, budget_s=1.0) is False
    assert launchctl.verbs(MCP_LABEL).count("print") == 5  # 1.0 с / 0.2 с


def test_bootout_budget_matches_bash_fifty_polls(launchctl):
    launchctl.loaded.add(MCP_LABEL)
    launchctl.sticky[MCP_LABEL] = 10**6

    launchd.bootout_and_wait(MCP_LABEL)
    assert launchctl.verbs(MCP_LABEL).count("print") == 50


# ── install / uninstall / restart ───────────────────────────────────────────
def test_install_writes_plists_and_bootstraps(launchctl, probe, layout):
    statuses = launchd.install(api_port=API_PORT, **layout)

    assert [s.label for s in statuses] == [MCP_LABEL, AGENT_LABEL, WEB_LABEL]
    assert all(s.loaded and s.answering and s.error is None for s in statuses)
    for label in (MCP_LABEL, AGENT_LABEL, WEB_LABEL):
        assert (layout["agents"] / f"{label}.plist").is_file()
        assert launchctl.verbs(label)[-1] == "bootstrap"
    assert launchctl.verbs(MCP_LABEL).index("bootout") < launchctl.verbs(MCP_LABEL).index(
        "bootstrap"
    )


def test_install_refuses_when_runtime_is_not_built(launchctl, probe, layout):
    (layout["runtime"] / "bin" / "cod-doc-mcp").unlink()

    statuses = launchd.install(api_port=API_PORT, **layout)

    assert all("рантайм не собран" in (s.error or "") for s in statuses)
    assert launchctl.calls == []
    assert not layout["agents"].exists()


def test_install_stops_after_first_bootstrap_failure(launchctl, probe, layout):
    launchctl.fail.add(AGENT_LABEL)

    statuses = launchd.install(api_port=API_PORT, **layout)

    by_label = {s.label: s for s in statuses}
    assert by_label[MCP_LABEL].loaded
    assert by_label[AGENT_LABEL].loaded is False
    assert "boom" in (by_label[AGENT_LABEL].error or "")
    assert AGENT_LABEL in (by_label[WEB_LABEL].skipped_reason or "")
    assert "bootstrap" not in launchctl.verbs(WEB_LABEL)


def test_install_drops_legacy_web_label(launchctl, probe, layout):
    layout["agents"].mkdir(parents=True)
    legacy = layout["agents"] / f"{launchd.LEGACY_WEB_LABEL}.plist"
    legacy.write_text("<plist/>", encoding="utf-8")
    launchctl.loaded.add(launchd.LEGACY_WEB_LABEL)

    launchd.install(api_port=API_PORT, **layout)

    assert "bootout" in launchctl.verbs(launchd.LEGACY_WEB_LABEL)
    assert not legacy.exists()
    assert (layout["agents"] / f"{launchd.LEGACY_WEB_LABEL}.plist.replaced").is_file()


def test_uninstall_boots_out_and_removes_plists(launchctl, layout):
    layout["agents"].mkdir(parents=True)
    for label in (MCP_LABEL, AGENT_LABEL, WEB_LABEL):
        (layout["agents"] / f"{label}.plist").write_text("<plist/>", encoding="utf-8")

    statuses = launchd.uninstall(agents=layout["agents"])

    assert all(not s.loaded for s in statuses)
    assert list(layout["agents"].iterdir()) == []
    assert [c[0] for c in launchctl.calls] == ["bootout"] * 3


def test_restart_kickstarts_every_label(launchctl):
    statuses = launchd.restart(api_port=API_PORT)

    assert all(s.kicked and s.loaded for s in statuses)
    assert [c[:2] for c in launchctl.calls] == [["kickstart", "-k"]] * 3


def test_restart_reports_not_loaded_service(launchctl):
    launchctl.fail.add(WEB_LABEL)

    by_label = {s.label: s for s in launchd.restart(api_port=API_PORT)}

    assert by_label[WEB_LABEL].kicked is False
    assert "не загружен" in (by_label[WEB_LABEL].error or "")


# ── status ──────────────────────────────────────────────────────────────────
def test_status_reports_loaded_and_answering(launchctl, probe):
    launchctl.loaded.update({MCP_LABEL, AGENT_LABEL})

    by_label = {s.label: s for s in launchd.status(api_port=API_PORT)}

    assert by_label[MCP_LABEL].loaded and by_label[MCP_LABEL].answering
    assert by_label[WEB_LABEL].loaded is False
    assert by_label[WEB_LABEL].answering is False
    assert len(probe.requests) == 2  # незагруженный веб не пробуем


def test_status_web_port_comes_from_api_port(launchctl, probe):
    """Порт веба — из конфига, а не из литерала в коде."""
    launchctl.loaded.add(WEB_LABEL)

    by_label = {s.label: s for s in launchd.status(api_port=9999)}

    assert by_label[WEB_LABEL].port == 9999
    assert by_label[MCP_LABEL].port == 8801
    assert by_label[AGENT_LABEL].port == 8802
    assert probe.requests[0].full_url == "http://127.0.0.1:9999/api/health"


def test_status_marks_loaded_but_silent_service(launchctl, monkeypatch):
    launchctl.loaded.add(MCP_LABEL)
    monkeypatch.setattr(launchd, "_http_ok", ProbeRecorder(ok=False))

    by_label = {s.label: s for s in launchd.status(api_port=API_PORT)}

    assert by_label[MCP_LABEL].loaded is True
    assert by_label[MCP_LABEL].answering is False


# ── пробы ───────────────────────────────────────────────────────────────────
def test_mcp_probe_sends_initialize(launchctl, probe):
    launchctl.loaded.add(MCP_LABEL)

    launchd.status(api_port=API_PORT)

    request = probe.requests[0]
    headers = {k.lower(): v for k, v in request.headers.items()}
    assert request.full_url == "http://127.0.0.1:8801/mcp"
    assert request.get_method() == "POST"
    assert headers["content-type"] == "application/json"
    assert headers["accept"] == "application/json, text/event-stream"
    assert b'"method":"initialize"' in request.data
    assert b'"protocolVersion":"2025-06-18"' in request.data


def test_web_probe_is_a_plain_health_get(launchctl, probe):
    launchctl.loaded.add(WEB_LABEL)

    launchd.status(api_port=API_PORT)

    request = probe.requests[0]
    assert request.full_url == f"http://127.0.0.1:{API_PORT}/api/health"
    assert request.get_method() == "GET"
    assert request.data is None


class _FakeResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.parametrize(("status_code", "expected"), [(200, True), (204, True), (302, False)])
def test_http_ok_accepts_only_2xx(status_code, expected, monkeypatch):
    monkeypatch.setattr(
        launchd.urllib.request, "urlopen", lambda _req, timeout=None: _FakeResponse(status_code)
    )
    request = urllib.request.Request("http://127.0.0.1:1/x")

    assert launchd._http_ok(request) is expected


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("connection refused"),
        urllib.error.HTTPError("http://x", 500, "boom", {}, None),
        OSError("broken pipe"),
    ],
)
def test_http_ok_swallows_transport_errors(exc, monkeypatch):
    def boom(_req, timeout=None):
        raise exc

    monkeypatch.setattr(launchd.urllib.request, "urlopen", boom)

    assert launchd._http_ok(urllib.request.Request("http://127.0.0.1:1/x")) is False


# ── не-macOS ────────────────────────────────────────────────────────────────
def test_is_supported_false_off_darwin(monkeypatch):
    monkeypatch.setattr(launchd.sys, "platform", "linux")
    assert launchd.is_supported() is False


def test_is_supported_false_without_launchctl(monkeypatch):
    monkeypatch.setattr(launchd.sys, "platform", "darwin")
    monkeypatch.setattr(launchd.shutil, "which", lambda _name: None)
    assert launchd.is_supported() is False


@pytest.fixture
def unsupported(monkeypatch):
    """Платформа без launchd; любой вызов `launchctl` здесь — ошибка теста."""
    monkeypatch.setattr(launchd, "is_supported", lambda: False)

    def forbidden(args: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"launchctl не должен зваться вне macOS: {args}")

    monkeypatch.setattr(launchd, "_run_launchctl", forbidden)


def test_restart_is_a_no_op_without_launchd(unsupported):
    statuses = launchd.restart(api_port=API_PORT)

    assert [s.skipped_reason for s in statuses] == [launchd.UNSUPPORTED_REASON] * 3
    assert all(not s.loaded and not s.kicked for s in statuses)


def test_status_is_a_no_op_without_launchd(unsupported):
    statuses = launchd.status(api_port=9999)

    assert all(s.skipped_reason == launchd.UNSUPPORTED_REASON for s in statuses)
    assert {s.label: s.port for s in statuses}[WEB_LABEL] == 9999


def test_install_is_a_no_op_without_launchd(unsupported, layout):
    statuses = launchd.install(api_port=API_PORT, **layout)

    assert all(s.skipped_reason == launchd.UNSUPPORTED_REASON for s in statuses)
    assert not layout["agents"].exists()


def test_uninstall_is_a_no_op_without_launchd(unsupported, tmp_path):
    statuses = launchd.uninstall(agents=tmp_path)

    assert all(s.skipped_reason == launchd.UNSUPPORTED_REASON for s in statuses)


def test_bootout_is_a_no_op_without_launchd(unsupported):
    assert launchd.bootout_and_wait(MCP_LABEL) is False
