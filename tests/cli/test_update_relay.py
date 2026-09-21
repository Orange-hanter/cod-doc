"""ADO-192: граница самозамены — родитель обязан отдать фазы B–D новому бинарю.

Дочерний процесс здесь настоящий, но это не venv, а sh-скрипт на десять строк:
он записывает свой ``$0``, свои аргументы и свой stdin в файлы, печатает
канонический JSON и возвращает заданный код. Собирать ради этого настоящий
рантайм значило бы проверять `uv`, а не relay.

Что именно проверяется:

* зовут по пути рантайма, а не через ``sys.executable`` — иначе фазы B–D
  исполнит старый код и накатит старую голову миграций;
* payload едет на stdin, а argv несёт ровно тот объём работ, который одобрил
  человек;
* ``--skip-install`` ребёнку не форвардится: свап уже позади;
* код выхода родителя — это код ребёнка;
* в resume-режиме подтверждения нет даже на терминале.
"""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING, Any

from click.testing import CliRunner

from cod_doc.cli import cmd_update, main
from cod_doc.config import Config, ProjectEntry
from cod_doc.services import runtime_service, update_service

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_SHA = "1234567890abcdef1234567890abcdef12345678"

#: Код, который вернёт фейковый ребёнок. Не 0, не 1 и не 3 — чтобы «родитель
#: отдаёт код ребёнка» нельзя было пройти случайно.
_CHILD_EXIT = 7

#: Фейковый ``<runtime>/bin/cod-doc``. Console-script рантайма — тоже sh-обёртка,
#: так что форма честная.
_CHILD_SCRIPT = """#!/bin/sh
printf '%s\\n' "$0" > @ARGV0@
printf '%s\\n' "$@" > @ARGV@
cat > @STDIN@
printf '%s\\n' '{"report": {"exit_code": @CODE@}}'
exit @CODE@
"""


class _Child:
    """Фейковый рантайм на диске плюс файлы, в которые он пишет свой вызов."""

    def __init__(self, root: Path, *, exit_code: int = _CHILD_EXIT) -> None:
        self.runtime = root / "runtime"
        self.argv0_file = root / "argv0.txt"
        self.argv_file = root / "argv.txt"
        self.stdin_file = root / "stdin.json"
        binary = self.runtime / "bin" / "cod-doc"
        binary.parent.mkdir(parents=True)
        binary.write_text(
            _CHILD_SCRIPT.replace("@ARGV0@", str(self.argv0_file))
            .replace("@ARGV@", str(self.argv_file))
            .replace("@STDIN@", str(self.stdin_file))
            .replace("@CODE@", str(exit_code)),
            encoding="utf-8",
        )
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    @property
    def argv0(self) -> str:
        return self.argv0_file.read_text(encoding="utf-8").strip()

    @property
    def argv(self) -> list[str]:
        return self.argv_file.read_text(encoding="utf-8").splitlines()

    @property
    def payload(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.stdin_file.read_text(encoding="utf-8"))
        return loaded


def _install_stub(src: Path) -> runtime_service.InstallStepReport:
    """Удачная фаза A: собрали, подменили, проверили — дальше relay."""
    return runtime_service.InstallStepReport(
        build=runtime_service.BuildReport(
            ref=_SHA, sha=_SHA, version="1.5.0", src=str(src), staged="/tmp/staged", ok=True
        ),
        swap=runtime_service.SwapReport(runtime="/tmp/runtime", previous="/tmp/runtime.previous"),
        versions=[
            runtime_service.InstallVersion(
                name="рантайм сервисов", binary="/tmp/runtime/bin/cod-doc", version="1.5.0"
            )
        ],
        ok=True,
    )


def _arrange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, exit_code: int = _CHILD_EXIT
) -> _Child:
    """Фейковый рантайм + план без git + фаза A без сборки."""
    child = _Child(tmp_path, exit_code=exit_code)
    monkeypatch.setenv("COD_DOC_RUNTIME", str(child.runtime))
    # Каталога нет: `drop_build_src` на несуществующем пути — no-op, и
    # временный worktree в тесте не нужен вовсе.
    src = tmp_path / "build" / "src"
    monkeypatch.setattr(runtime_service, "install_runtime", lambda *a, **k: _install_stub(src))

    def fake_plan(
        cfg: Config,
        *,
        ref: str | None,
        runtime: Path,
        repo: Path | None,
        projects: list[str] | None,
        ttl_minutes: int = 30,
        skip: update_service.SkipFlags | None = None,
    ) -> update_service.UpdatePlan:
        return update_service.UpdatePlan(
            ref=ref or "origin/main",
            target_sha=_SHA,
            runtime=runtime,
            repo=repo or tmp_path / "repo",
            projects=list(projects or []),
            skip=skip or update_service.SkipFlags(),
            ttl_minutes=ttl_minutes,
        )

    monkeypatch.setattr(update_service, "plan", fake_plan)
    return child


def _register(tmp_path: Path, name: str) -> None:
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    Config.load().add_project(ProjectEntry(name=name, path=str(root)))


def test_parent_calls_the_runtime_binary_not_its_own_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В этом весь смысл relay: миграции обязан катить новый код."""
    child = _arrange(tmp_path, monkeypatch)

    result = CliRunner().invoke(main, ["update", "--yes"])

    assert result.exit_code == _CHILD_EXIT, result.output
    assert child.argv0 == str(child.runtime / "bin" / "cod-doc")


def test_payload_arrives_on_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Отчёт фазы A едет через stdin — argv для него был бы слишком узок."""
    child = _arrange(tmp_path, monkeypatch)

    CliRunner().invoke(main, ["update", "--yes"])

    payload = child.payload
    assert payload["build"]["sha"] == _SHA
    assert payload["swap"]["previous"] == "/tmp/runtime.previous"
    assert payload["src"] == str(tmp_path / "build" / "src")


def test_child_is_called_in_resume_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    child = _arrange(tmp_path, monkeypatch)

    CliRunner().invoke(main, ["update", "--yes"])

    assert child.argv[:3] == [
        update_service.UPDATE_COMMAND,
        update_service.RESUME_FLAG,
        update_service.RESUME_STDIN,
    ]


def test_only_the_agreed_flags_are_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--skip-install`` ребёнку не нужен: свап уже позади."""
    child = _arrange(tmp_path, monkeypatch)
    _register(tmp_path, "alpha")

    result = CliRunner().invoke(
        main,
        [
            "update",
            "--yes",
            "-p",
            "alpha",
            "--ttl-minutes",
            "7",
            "--skip-migrate",
            "--skip-repair",
            "--skip-links",
        ],
    )

    assert result.exit_code == _CHILD_EXIT, result.output
    assert child.argv[3:] == [
        update_service.FLAG_PROJECT,
        "alpha",
        update_service.FLAG_TTL_MINUTES,
        "7",
        update_service.FLAG_SKIP_MIGRATE,
        update_service.FLAG_SKIP_REPAIR,
        update_service.FLAG_SKIP_LINKS,
    ]
    assert "--skip-install" not in child.argv


def test_parent_exits_with_the_child_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Фазы B–D исполнил ребёнок — его код единственное, что о них знает родитель."""
    _arrange(tmp_path, monkeypatch, exit_code=update_service.EXIT_FAILED)

    result = CliRunner().invoke(main, ["update", "--yes"])

    assert result.exit_code == update_service.EXIT_FAILED, result.output


def test_skip_install_does_not_relay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Без свапа P0 и есть правильный код: фазы B–D он делает сам."""
    child = _arrange(tmp_path, monkeypatch)
    seen: list[dict[str, Any]] = []

    def fake_post_swap(cfg: Config, **kwargs: Any) -> update_service.UpdateReport:
        seen.append(kwargs)
        return update_service.UpdateReport()

    monkeypatch.setattr(update_service, "run_post_swap", fake_post_swap)

    result = CliRunner().invoke(main, ["update", "--yes", "--skip-install"])

    assert result.exit_code == update_service.EXIT_OK, result.output
    assert not child.argv0_file.exists()
    assert seen and seen[0]["resumed"] == {}


# ── resume-режим ────────────────────────────────────────────────────────────


def test_resume_reads_stdin_and_never_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Человек уже ответил родителю — вопроса нет даже на терминале."""
    asked: list[str] = []
    monkeypatch.setattr(cmd_update, "_interactive", lambda: True)
    monkeypatch.setattr(cmd_update.click, "confirm", lambda *a, **k: asked.append("да"))
    seen: list[dict[str, Any]] = []

    def fake_post_swap(cfg: Config, **kwargs: Any) -> update_service.UpdateReport:
        seen.append(kwargs)
        return update_service.UpdateReport(install=kwargs["resumed"])

    monkeypatch.setattr(update_service, "run_post_swap", fake_post_swap)
    payload = {"build": {"sha": _SHA}, "src": str(tmp_path / "src")}

    result = CliRunner().invoke(
        main,
        ["update", "--resume-json", "-", "--ttl-minutes", "5"],
        input=json.dumps(payload),
    )

    assert result.exit_code == update_service.EXIT_OK, result.output
    assert asked == []
    assert seen[0]["resumed"] == payload
    assert seen[0]["ttl_minutes"] == 5


def test_resume_keeps_stdout_for_the_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stdout принадлежит родителю: отчёт ребёнка уходит в stderr."""
    monkeypatch.setattr(
        update_service,
        "run_post_swap",
        lambda cfg, **kwargs: update_service.UpdateReport(
            phases=[update_service.PhaseReport(name="migrate", ok=True)]
        ),
    )

    result = CliRunner().invoke(main, ["update", "--resume-json", "-"], input="{}")

    assert result.exit_code == update_service.EXIT_OK, result.output
    assert result.stdout == ""
    assert "migrate" in result.stderr


def test_resume_rejects_garbage_from_the_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        update_service,
        "run_post_swap",
        lambda cfg, **kwargs: called.append("run") or update_service.UpdateReport(),
    )

    result = CliRunner().invoke(main, ["update", "--resume-json", "-"], input="не json")

    assert result.exit_code == update_service.EXIT_FAILED
    assert called == []


def test_resume_reads_a_file_when_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--resume-json <path>`` — та же дверь, что и ``-``, но из файла."""
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        update_service,
        "run_post_swap",
        lambda cfg, **kwargs: seen.append(kwargs) or update_service.UpdateReport(),
    )
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps({"src": "/tmp/x"}), encoding="utf-8")

    result = CliRunner().invoke(main, ["update", "--resume-json", str(payload_file)])

    assert result.exit_code == update_service.EXIT_OK, result.output
    assert seen[0]["resumed"] == {"src": "/tmp/x"}


def test_resume_is_hidden_from_help() -> None:
    """Внутренний вход relay не предлагается человеку."""
    result = CliRunner().invoke(main, ["update", "--help"])

    assert "--resume-json" not in result.output
