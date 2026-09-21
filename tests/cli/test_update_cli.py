"""ADO-192: поверхность ``cod-doc update`` и ``cod-doc runtime``.

Сервисы здесь подменены целиком: проверяется ровно то, чем владеет CLI —
подтверждение, коды выхода, потоки и рендер. Сборку рантайма, миграции и
починку проверяют тесты слоя services.

Главное, что стережёт файл: **ни один мутатор не зовётся, пока человек не
согласился**. Поэтому в каждом отказном кейсе проверяется не только код
выхода, но и пустой список вызовов.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from click.testing import CliRunner

from cod_doc.cli import cmd_update, main
from cod_doc.config import Config, ProjectEntry
from cod_doc.services import launchd_service, repair_service, runtime_service, update_service

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

#: Узкий терминал — худший случай для переноса строк rich (ADO-176).
_NARROW = "40"

#: Длиннее 80 колонок: ровно на такой длине rich ставил перенос внутрь значения.
_LONG_RUNTIME = (
    "/Users/somebody/Library/Application Support/cod-doc/runtime-с-очень-длинным-"
    "именем-каталога-чтобы-перевалить-за-восемьдесят-колонок"
)

_SHA = "1234567890abcdef1234567890abcdef12345678"


class _Calls:
    """Счётчик вызовов мутирующих функций сервиса."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def record(self, name: str) -> None:
        self.names.append(name)


def _plan_stub(
    recorded: list[dict[str, Any]],
    *,
    runtime_path: str | None = None,
    projects_override: list[str] | None = None,
) -> Callable[..., update_service.UpdatePlan]:
    """Фейковый ``update_service.plan``: возвращает план из того, что пришло из CLI."""

    def fake(
        cfg: Config,
        *,
        ref: str | None,
        runtime: Path,
        repo: Path | None,
        projects: list[str] | None,
        ttl_minutes: int = repair_service.DEFAULT_TTL_MINUTES,
        skip: update_service.SkipFlags | None = None,
    ) -> update_service.UpdatePlan:
        recorded.append(
            {
                "ref": ref,
                "runtime": runtime,
                "repo": repo,
                "projects": projects,
                "ttl_minutes": ttl_minutes,
                "skip": skip,
            }
        )
        return update_service.UpdatePlan(
            ref=ref or "origin/main",
            target_sha=_SHA,
            current_version="1.4.1",
            runtime=Path(runtime_path) if runtime_path else runtime,
            repo=repo,
            projects=projects_override if projects_override is not None else list(projects or []),
            skip=skip or update_service.SkipFlags(),
            ttl_minutes=ttl_minutes,
        )

    return fake


def _report(install: update_service.InstallReport | None = None) -> update_service.UpdateReport:
    return update_service.UpdateReport(
        phases=[update_service.PhaseReport(name="install", ok=True, duration_s=1.5)],
        install=install,
    )


def _no_mutators(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    """Подменить оба входа сервиса, которые что-то меняют."""
    calls = _Calls()

    def fake_run(*_args: object, **_kwargs: object) -> update_service.UpdateReport:
        calls.record("run")
        return _report()

    def fake_resume(*_args: object, **_kwargs: object) -> update_service.UpdateReport:
        calls.record("run_post_swap")
        return _report()

    monkeypatch.setattr(update_service, "run", fake_run)
    monkeypatch.setattr(update_service, "run_post_swap", fake_resume)
    return calls


# ── подтверждение ───────────────────────────────────────────────────────────


def test_non_tty_without_yes_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Молча продолжить нельзя, зависнуть под launchd — тем более."""
    calls = _no_mutators(monkeypatch)
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))

    result = CliRunner().invoke(main, ["update"])

    assert result.exit_code == update_service.EXIT_USAGE, result.output
    assert "--yes" in result.output
    assert "--dry-run" in result.output
    assert calls.names == []


def test_answering_no_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """«Нет» на единственный вопрос — выход 0 и ни одного вызова мутатора."""
    calls = _no_mutators(monkeypatch)
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))
    monkeypatch.setattr(cmd_update, "_interactive", lambda: True)

    result = CliRunner().invoke(main, ["update"], input="n\n")

    assert result.exit_code == update_service.EXIT_OK, result.output
    assert calls.names == []


def test_yes_runs_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _no_mutators(monkeypatch)
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))

    result = CliRunner().invoke(main, ["update", "--yes"])

    assert result.exit_code == update_service.EXIT_OK, result.output
    assert calls.names == ["run"]


def test_dry_run_prints_the_plan_and_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _no_mutators(monkeypatch)
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))

    result = CliRunner().invoke(main, ["update", "--dry-run"])

    assert result.exit_code == update_service.EXIT_OK, result.output
    assert "План обновления" in result.stdout
    assert "Обратимо" in result.stdout
    assert calls.names == []


def test_plan_names_the_phases_and_the_db_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Колонка «Обратимо» и предупреждение про БД — предмет вопроса, не декор."""
    _no_mutators(monkeypatch)
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))

    result = CliRunner().invoke(main, ["update", "--dry-run"])

    for phase in ("A · рантайм", "B · миграции", "C · сервисы", "D · починка"):
        assert phase in result.stdout
    assert "БД" in result.stdout


# ── потоки и коды выхода ────────────────────────────────────────────────────


def test_json_without_yes_or_dry_run_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--json`` — это неинтерактивный запуск, спрашивать будет некого."""
    calls = _no_mutators(monkeypatch)
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))

    result = CliRunner().invoke(main, ["update", "--json"])

    assert result.exit_code == update_service.EXIT_USAGE, result.output
    assert "--yes" in result.output
    assert calls.names == []


def test_json_stdout_is_one_object_in_a_narrow_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Зеркало ADO-176: длинный путь в узком терминале не должен рвать JSON."""
    monkeypatch.setenv("COLUMNS", _NARROW)
    monkeypatch.setattr(update_service, "plan", _plan_stub([], runtime_path=_LONG_RUNTIME))
    install = update_service.InstallReport(
        build=runtime_service.BuildReport(
            ref="origin/main",
            sha=_SHA,
            version="1.5.0",
            src=f"{_LONG_RUNTIME}/src",
            staged=f"{_LONG_RUNTIME}.staged",
            ok=True,
        )
    )
    monkeypatch.setattr(update_service, "run", lambda *a, **k: _report(install))

    result = CliRunner().invoke(main, ["update", "--json", "--yes"])

    assert result.exit_code == update_service.EXIT_OK, result.output
    payload = json.loads(result.stdout)
    assert payload["plan"]["runtime"] == _LONG_RUNTIME
    assert payload["report"]["install"]["build"]["staged"] == f"{_LONG_RUNTIME}.staged"


def test_json_dry_run_is_one_object(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _no_mutators(monkeypatch)
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))

    result = CliRunner().invoke(main, ["update", "--json", "--dry-run"])

    assert result.exit_code == update_service.EXIT_OK, result.output
    payload = json.loads(result.stdout)
    assert payload["report"] is None
    assert payload["plan"]["has_irreversible"] is True
    assert calls.names == []


def test_exit_code_comes_from_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Код дочернего процесса уже внутри отчёта — CLI его не пересчитывает."""
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))
    report = _report()
    report.child_exit_code = update_service.EXIT_FAILED
    monkeypatch.setattr(update_service, "run", lambda *a, **k: report)

    result = CliRunner().invoke(main, ["update", "--yes"])

    assert result.exit_code == update_service.EXIT_FAILED


def test_rolled_back_run_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))
    report = _report()
    report.rolled_back = True
    monkeypatch.setattr(update_service, "run", lambda *a, **k: report)

    result = CliRunner().invoke(main, ["update", "--yes"])

    assert result.exit_code == update_service.EXIT_ROLLED_BACK
    assert "откач" in result.stdout


# ── ошибки вызова ───────────────────────────────────────────────────────────


def test_unknown_project_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _no_mutators(monkeypatch)
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(update_service, "plan", _plan_stub(recorded))

    result = CliRunner().invoke(main, ["update", "--yes", "-p", "нет-такого"])

    assert result.exit_code == update_service.EXIT_USAGE, result.output
    assert "нет-такого" in result.output
    assert recorded == []
    assert calls.names == []


def test_known_project_reaches_the_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_mutators(monkeypatch)
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(update_service, "plan", _plan_stub(recorded))
    root = tmp_path / "alpha"
    root.mkdir()
    Config.load().add_project(ProjectEntry(name="alpha", path=str(root)))

    result = CliRunner().invoke(main, ["update", "--yes", "-p", "alpha"])

    assert result.exit_code == update_service.EXIT_OK, result.output
    assert recorded[0]["projects"] == ["alpha"]


def test_update_lock_is_printed_as_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Текст замка уже содержит подсказку — печатаем его, а не стек."""
    monkeypatch.setattr(update_service, "plan", _plan_stub([]))

    def locked(*_args: object, **_kwargs: object) -> update_service.UpdateReport:
        raise update_service.UpdateLocked("уже идёт другой прогон cod-doc update (замок /tmp/l)")

    monkeypatch.setattr(update_service, "run", locked)

    result = CliRunner().invoke(main, ["update", "--yes"])

    assert result.exit_code == update_service.EXIT_FAILED
    assert "уже идёт другой прогон" in result.output
    assert "Traceback" not in result.output


def test_runtime_error_from_plan_is_printed_as_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """«Негде взять исходники» — диагноз с выходом, а не traceback."""
    calls = _no_mutators(monkeypatch)

    def broken(*_args: object, **_kwargs: object) -> update_service.UpdatePlan:
        raise runtime_service.RuntimeError_("негде взять исходники cod-doc: нет чекаута")

    monkeypatch.setattr(update_service, "plan", broken)

    result = CliRunner().invoke(main, ["update", "--yes"])

    assert result.exit_code == update_service.EXIT_FAILED
    assert "негде взять исходники" in result.output
    assert "Traceback" not in result.output
    assert calls.names == []


def test_update_and_upgrade_are_the_same_command() -> None:
    """Две команды с одним поведением обречены разъехаться — объект один."""
    assert main.commands["upgrade"] is main.commands["update"]


# ── cod-doc runtime ─────────────────────────────────────────────────────────


def test_runtime_version_lists_three_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Вопрос «какая у меня версия» не имеет одного ответа — отвечаем за все."""
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("COD_DOC_REPO", str(repo))
    monkeypatch.setenv("COD_DOC_RUNTIME", str(tmp_path / "runtime"))
    seen: dict[str, Any] = {}

    def versions(*, runtime: Path, repo: Path | None) -> list[runtime_service.InstallVersion]:
        seen["runtime"], seen["repo"] = runtime, repo
        return [
            runtime_service.InstallVersion(name=name, binary=f"/bin/{name}", version="1.4.1")
            for name in ("рантайм сервисов", "PATH (uv tool)", "репозиторий (editable)")
        ]

    monkeypatch.setattr(runtime_service, "installed_versions", versions)

    result = CliRunner().invoke(main, ["runtime", "version"])

    assert result.exit_code == 0, result.output
    assert seen["runtime"] == tmp_path / "runtime"
    assert seen["repo"] == repo
    for name in ("рантайм", "PATH", "репозиторий"):
        assert name in result.stdout


def test_runtime_rollback_warns_that_the_db_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    """Откат вернул код, но не схему. Об этом обязан сказать вызывающий."""
    monkeypatch.setattr(
        runtime_service,
        "rollback",
        lambda runtime: runtime_service.SwapReport(
            runtime=str(runtime), previous=None, rolled_back=True
        ),
    )
    monkeypatch.setattr(launchd_service, "restart", lambda *, api_port: [])

    result = CliRunner().invoke(main, ["runtime", "rollback"])

    assert result.exit_code == 0, result.output
    assert "БД" in result.stdout
    assert "миграции" in result.stdout


def test_runtime_status_json_is_parseable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        launchd_service,
        "status",
        lambda *, api_port: [
            launchd_service.ServiceStatus(
                label="com.cod-doc.mcp",
                port=8801,
                profile="standard",
                loaded=True,
                answering=True,
            )
        ],
    )
    monkeypatch.setattr(runtime_service, "installed_versions", lambda **_kwargs: [])

    result = CliRunner().invoke(main, ["runtime", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["services"][0]["label"] == "com.cod-doc.mcp"
