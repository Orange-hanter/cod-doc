"""ADO-192: оркестратор `cod-doc update` (`cod_doc/services/update_service.py`).

Фазы здесь мокаются целиком: сборка рантайма, launchctl, alembic и починка
проверяются своими тестами, а этот модуль отвечает за другое — за порядок,
за границу свапа и за код выхода. Ровно те места, где ошибка стоит дорого:
откат после провала миграций оставил бы старый код на новой схеме, а
пропущенный relay — новый бинарь без догнанной БД.

Единственное, что гоняется по-настоящему, — сам relay (поддельный sh-скрипт
на месте `<runtime>/bin/cod-doc`) и `flock`: и то и другое мок проверял бы
сам себя.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import SchemaMismatchError
from cod_doc.services import (
    launchd_service,
    project_service,
    repair_service,
    runtime_service,
    update_service,
)
from cod_doc.services.update_service import SkipFlags, UpdatePlan

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
PROJECTS = ["alpha", "beta"]


# ── дублёры фаз ─────────────────────────────────────────────────────────────
@dataclass
class _Calls:
    """Журнал вызовов всех четырёх фаз — по нему же проверяется их порядок."""

    order: list[str] = field(default_factory=list)
    install: list[tuple[str, str]] = field(default_factory=list)
    relay: list[dict] = field(default_factory=list)
    migrate: list[str] = field(default_factory=list)
    restart: list[int] = field(default_factory=list)
    repair: list[str] = field(default_factory=list)
    diagnose: list[str] = field(default_factory=list)
    rollback: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    path_tool: list[str] = field(default_factory=list)


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def _install_step(
    *, ok: bool = True, rolled_back: bool = False
) -> runtime_service.InstallStepReport:
    build = runtime_service.BuildReport(
        ref=SHA,
        sha=SHA,
        version="1.4.2",
        src="/tmp/cod-doc-build-x/src",
        staged="/runtime.staged",
        ok=True,
    )
    swap = runtime_service.SwapReport(
        runtime="/runtime",
        previous="/runtime.previous",
        rolled_back=rolled_back,
        ok=not rolled_back,
        error="рантайм не работает по конечному пути" if rolled_back else None,
    )
    return runtime_service.InstallStepReport(
        build=build,
        swap=swap,
        versions=[
            runtime_service.InstallVersion(
                name="рантайм сервисов", binary="/runtime/bin/cod-doc", version="1.4.2"
            )
        ],
        ok=ok,
        error=None if ok else "свап откачен",
    )


def _statuses(*, kicked: bool = True) -> list[launchd_service.ServiceStatus]:
    return [
        launchd_service.ServiceStatus(
            label=spec.label,
            port=spec.port,
            profile=spec.profile,
            loaded=kicked,
            answering=False,
            kicked=kicked,
            error=None if kicked else f"не загружен: {spec.label}",
        )
        for spec in launchd_service.SERVICES
    ]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    """Подменяет все четыре фазы и вход в БД проекта; пишет порядок вызовов."""
    log = _Calls()

    def install_runtime(
        repo, sha, *, runtime, python_version=runtime_service.DEFAULT_PYTHON_VERSION
    ):
        log.order.append("install")
        log.install.append((str(repo), sha))
        return _install_step()

    def relay(*, runtime, payload, argv_tail):
        log.order.append("relay")
        log.relay.append({"runtime": str(runtime), "payload": payload, "argv_tail": argv_tail})
        return SimpleNamespace(returncode=update_service.EXIT_OK)

    def migrate_entry(entry):
        log.order.append("migrate")
        log.migrate.append(entry.name)
        return project_service.MigrateResult(name=entry.name, db_url="sqlite://", ok=True)

    def migrate_all(cfg):
        return [migrate_entry(entry) for entry in cfg.list_projects()]

    def restart(*, api_port):
        log.order.append("services")
        log.restart.append(api_port)
        return _statuses()

    def apply_repair(session, *, project_id, root_path, master_path, slug, **kwargs):
        log.order.append("repair")
        log.repair.append(slug)
        return repair_service.RepairResult(project=slug, dry_run=False)

    def diagnose(session, *, project_id, root_path, master_path, slug, **kwargs):
        log.diagnose.append(slug)
        return repair_service.RepairPlan(project=slug)

    def rollback(runtime):
        log.rollback.append(str(runtime))
        return runtime_service.SwapReport(runtime=str(runtime), previous=None, rolled_back=True)

    monkeypatch.setattr(runtime_service, "install_runtime", install_runtime)
    monkeypatch.setattr(runtime_service, "rollback", rollback)
    monkeypatch.setattr(runtime_service, "rollback_failed_swap", rollback)
    monkeypatch.setattr(runtime_service, "drop_build_src", lambda src: log.dropped.append(str(src)))
    monkeypatch.setattr(
        runtime_service, "sync_path_tool", lambda src: (log.path_tool.append(str(src)), "1.4.2")[1]
    )
    monkeypatch.setattr(update_service, "relay_to_new_runtime", relay)
    monkeypatch.setattr(project_service, "migrate_entry", migrate_entry)
    monkeypatch.setattr(project_service, "migrate_registered_projects", migrate_all)
    monkeypatch.setattr(launchd_service, "restart", restart)
    monkeypatch.setattr(repair_service, "apply", apply_repair)
    monkeypatch.setattr(repair_service, "diagnose", diagnose)
    return log


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Вход в БД проекта без БД. Слаг, добавленный в множество, «отстал схемой»."""
    behind: set[str] = set()

    def db_for_entry(entry):
        if entry.name in behind:
            raise SchemaMismatchError(f"schema mismatch for {entry.name!r}")
        return SimpleNamespace(), _FakeEngine()

    @contextmanager
    def transactional(factory, *, commit=True):
        yield SimpleNamespace()

    monkeypatch.setattr(update_service, "db_for_entry", db_for_entry)
    monkeypatch.setattr(update_service, "transactional", transactional)
    monkeypatch.setattr(
        update_service,
        "ProjectRepository",
        lambda session: SimpleNamespace(get_by_slug=lambda slug: SimpleNamespace(row_id=1)),
    )
    return behind


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        projects=[
            ProjectEntry(name=name, path=str(tmp_path / name)).model_dump() for name in PROJECTS
        ]
    )


def _plan(
    tmp_path, *, skip: SkipFlags | None = None, projects: list[str] | None = None
) -> UpdatePlan:
    return UpdatePlan(
        ref="origin/main",
        target_sha=SHA,
        runtime=tmp_path / "runtime",
        repo=tmp_path / "repo",
        projects=list(projects if projects is not None else PROJECTS),
        skip=skip or SkipFlags(),
    )


def _phases(report: update_service.UpdateReport) -> dict[str, update_service.PhaseReport]:
    return {phase.name: phase for phase in report.phases}


# ── пропуск фаз ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("skip_migrate", [False, True])
@pytest.mark.parametrize("skip_repair", [False, True])
def test_skip_flags_skip_the_phase_and_call_nothing(
    tmp_path, cfg, calls, db, skip_migrate, skip_repair
):
    """Каждый флаг гасит свою фазу с причиной — и не зовёт подлежащую функцию."""
    flags = SkipFlags(install=True, migrate=skip_migrate, repair=skip_repair)
    report = update_service.run(cfg, update_plan=_plan(tmp_path, skip=flags))
    phases = _phases(report)

    assert phases["install"].skipped and phases["install"].skip_reason
    assert calls.install == []
    assert calls.relay == []
    # Рантайм не менялся — демонов не трогаем.
    assert phases["services"].skipped and phases["services"].skip_reason
    assert calls.restart == []

    assert phases["migrate"].skipped is skip_migrate
    assert bool(calls.migrate) is not skip_migrate
    assert phases["repair"].skipped is skip_repair
    assert bool(calls.repair) is not skip_repair
    if skip_migrate:
        assert phases["migrate"].skip_reason
    if skip_repair:
        assert phases["repair"].skip_reason
    assert report.exit_code == update_service.EXIT_OK


def test_skip_install_means_no_relay(tmp_path, cfg, calls, db):
    """Свапа не было — передавать управление некому: P0 и есть правильный код."""
    update_service.run(cfg, update_plan=_plan(tmp_path, skip=SkipFlags(install=True)))
    assert calls.relay == []
    assert calls.migrate == PROJECTS


def test_successful_swap_always_relays(tmp_path, cfg, calls, db):
    """Даже с --skip-migrate --skip-repair: одна ветка кода вместо двух."""
    flags = SkipFlags(migrate=True, repair=True)
    report = update_service.run(cfg, update_plan=_plan(tmp_path, skip=flags))

    assert len(calls.relay) == 1
    tail = calls.relay[0]["argv_tail"]
    assert update_service.FLAG_SKIP_MIGRATE in tail
    assert update_service.FLAG_SKIP_REPAIR in tail
    # Фазы B–D делает ребёнок, поэтому в отчёте родителя их нет.
    assert calls.migrate == [] and calls.repair == []
    assert report.exit_code == update_service.EXIT_OK


# ── коды выхода ─────────────────────────────────────────────────────────────
def test_migrate_failure_exits_failed_and_never_rolls_back(tmp_path, cfg, calls, db, monkeypatch):
    """Откат после провала миграций дал бы старый код на новой схеме — хуже, чем было."""
    monkeypatch.setattr(
        project_service,
        "migrate_entry",
        lambda entry: project_service.MigrateResult(
            name=entry.name, db_url="sqlite://", ok=False, error="OperationalError: boom"
        ),
    )
    report = update_service.run_post_swap(cfg, resumed={}, projects=PROJECTS)

    assert report.exit_code == update_service.EXIT_FAILED
    assert calls.rollback == []
    assert _phases(report)["migrate"].error and "boom" in _phases(report)["migrate"].error
    # Упавшая миграция не отменяет починку остальных фаз.
    assert calls.repair == PROJECTS


def test_failed_verify_rolls_back_and_skips_b_and_d(tmp_path, cfg, calls, db, monkeypatch):
    """Плохая сборка: рантайм вернулся, код выхода 3, дальше фаз нет."""
    monkeypatch.setattr(
        runtime_service,
        "install_runtime",
        lambda repo, sha, **kw: _install_step(ok=False, rolled_back=True),
    )
    report = update_service.run(cfg, update_plan=_plan(tmp_path))

    assert report.rolled_back is True
    assert report.exit_code == update_service.EXIT_ROLLED_BACK
    assert calls.relay == []
    for name in ("migrate", "repair"):
        assert _phases(report)[name].skipped
        assert _phases(report)[name].skip_reason


def test_child_exit_code_reaches_the_caller(tmp_path, cfg, calls, db, monkeypatch):
    """Родитель обязан выйти кодом ребёнка — иначе провал фаз B–D невидим."""
    monkeypatch.setattr(
        update_service,
        "relay_to_new_runtime",
        lambda **kw: SimpleNamespace(returncode=update_service.EXIT_FAILED),
    )
    report = update_service.run(cfg, update_plan=_plan(tmp_path))
    assert report.exit_code == update_service.EXIT_FAILED
    assert report.rolled_back is False


# ── порядок ─────────────────────────────────────────────────────────────────
def test_parent_swaps_then_relays_and_nothing_else(tmp_path, cfg, calls, db):
    """Между свапом и relay у родителя не должно быть ни одной фазы."""
    update_service.run(cfg, update_plan=_plan(tmp_path))
    assert calls.order == ["install", "relay"]


def test_child_migrates_then_restarts_then_repairs(tmp_path, cfg, calls, db):
    """Демоны поднимаются уже на догнанной схеме — иначе SchemaMismatchError в лоб."""
    update_service.run_post_swap(cfg, resumed={"src": None}, projects=["alpha"])
    assert calls.order == ["migrate", "services", "repair"]


def test_build_src_dies_only_after_the_relay(tmp_path, cfg, calls, db):
    """`uv tool install` в ребёнке ставит PATH-установку из этого worktree."""
    update_service.run(cfg, update_plan=_plan(tmp_path))
    src = calls.relay[0]["payload"]["src"]
    assert src == "/tmp/cod-doc-build-x/src"
    assert calls.dropped == [src]


# ── relay по-настоящему ─────────────────────────────────────────────────────
def test_relay_runs_the_runtime_binary_with_payload_on_stdin(tmp_path):
    """Зовём `<runtime>/bin/cod-doc`, а не sys.executable: в этом весь смысл relay."""
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    binary = runtime / "bin" / "cod-doc"
    stdin_dump = tmp_path / "stdin.json"
    argv_dump = tmp_path / "argv.txt"
    binary.write_text(
        "#!/bin/sh\n"
        f'cat > "{stdin_dump}"\n'
        f'printf "%s\\n" "$@" > "{argv_dump}"\n'
        "echo '{\"exit_code\": 3}'\n"
        "exit 3\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)

    proc = update_service.relay_to_new_runtime(
        runtime=runtime,
        payload={"build": {"sha": SHA}, "src": "/tmp/src"},
        argv_tail=[update_service.FLAG_PROJECT, "alpha"],
    )

    assert proc.returncode == update_service.EXIT_ROLLED_BACK
    assert json.loads(stdin_dump.read_text(encoding="utf-8")) == {
        "build": {"sha": SHA},
        "src": "/tmp/src",
    }
    assert argv_dump.read_text(encoding="utf-8").split() == [
        "update",
        "--resume-json",
        "-",
        "--project",
        "alpha",
    ]


def test_missing_binary_is_a_report_not_a_traceback(tmp_path, cfg, db, monkeypatch):
    """Бинаря по конечному пути нет — это диагноз в отчёте, а не OSError наружу.

    Relay здесь настоящий: подменён только свап, а `<runtime>/bin/cod-doc` не
    существует — ровно то, что увидит человек после сломанной сборки.
    """
    monkeypatch.setattr(runtime_service, "install_runtime", lambda repo, sha, **kw: _install_step())
    monkeypatch.setattr(runtime_service, "drop_build_src", lambda src: None)
    report = update_service.run(cfg, update_plan=_plan(tmp_path))

    assert report.exit_code == update_service.EXIT_FAILED
    relay_phase = _phases(report)["relay"]
    assert relay_phase.ok is False
    assert "bin/cod-doc" in (relay_phase.error or "")


# ── фаза D ──────────────────────────────────────────────────────────────────
def test_schema_mismatch_on_one_project_does_not_stop_the_others(tmp_path, cfg, calls, db):
    """Отставшая БД одного проекта — строка в отчёте, остальные чинятся."""
    db.add("alpha")
    report = update_service.run_post_swap(
        cfg, resumed={}, projects=PROJECTS, skip=SkipFlags(migrate=True)
    )

    assert calls.repair == ["beta"]
    failed = next(result for result in report.repair if result.project == "alpha")
    assert update_service.SCHEMA_BEHIND_HINT in failed.errors[0]
    assert report.exit_code == update_service.EXIT_FAILED


def test_without_a_selection_the_whole_registry_is_migrated(tmp_path, cfg, calls, db):
    """Проектов не назвали — значит, все: реестр и есть скоуп обновления."""
    report = update_service.run_post_swap(cfg, resumed={}, projects=None)
    assert calls.migrate == PROJECTS
    assert [result.project for result in report.repair] == PROJECTS


def test_services_absent_from_launchd_are_not_a_failed_update(
    tmp_path, cfg, calls, db, monkeypatch
):
    """`kickstart` отвечает «не загружен» на все три лейбла — их просто не ставили."""
    monkeypatch.setattr(launchd_service, "restart", lambda *, api_port: _statuses(kicked=False))
    report = update_service.run_post_swap(cfg, resumed={"src": None}, projects=["alpha"])

    services = _phases(report)["services"]
    assert services.skipped and services.skip_reason
    assert report.exit_code == update_service.EXIT_OK


def test_one_dead_service_is_a_failed_update(tmp_path, cfg, calls, db, monkeypatch):
    """Два демона поднялись, третий нет — это провал, а не «сервисов нет»."""
    mixed = _statuses()
    mixed[-1].kicked = False
    mixed[-1].loaded = False
    monkeypatch.setattr(launchd_service, "restart", lambda *, api_port: mixed)
    report = update_service.run_post_swap(cfg, resumed={"src": None}, projects=["alpha"])

    services = _phases(report)["services"]
    assert services.ok is False
    assert mixed[-1].label in (services.error or "")
    assert report.exit_code == update_service.EXIT_FAILED


def test_unknown_project_is_named_not_silently_dropped(tmp_path, cfg, calls, db):
    report = update_service.run_post_swap(cfg, resumed={}, projects=["ghost"])
    assert [result.project for result in report.repair] == ["ghost"]
    assert [result.name for result in report.migrate] == ["ghost"]
    assert report.migrate[0].ok is False


# ── отчёт ───────────────────────────────────────────────────────────────────
def test_to_dict_survives_json_dumps(tmp_path, cfg, calls, db):
    """Ни Path, ни datetime, ни dataclass наружу: отчёт печатается как есть."""
    parent = update_service.run(cfg, update_plan=_plan(tmp_path))
    child = update_service.run_post_swap(
        cfg, resumed={"build": {"sha": SHA}, "src": str(tmp_path)}, projects=PROJECTS
    )

    for report in (parent, child):
        dumped = json.loads(json.dumps(report.to_dict()))
        assert dumped["exit_code"] == report.exit_code
    assert parent.to_dict()["install"]["build"]["version"] == "1.4.2"
    # Дочерний отчёт дописывает в тот же словарь свои ключи.
    assert child.to_dict()["install"]["services"][0]["label"] == "com.cod-doc.mcp"
    assert child.to_dict()["install"]["path_tool"]["version"] == "1.4.2"
    assert calls.path_tool == [str(tmp_path)]


def test_human_readable_ref_replaces_sha_in_the_build_report(tmp_path, cfg, calls, db):
    """`build_staged` знает только sha — ревизию человека дописывает оркестратор."""
    report = update_service.run(cfg, update_plan=_plan(tmp_path))
    assert report.install.build.ref == "origin/main"
    assert report.install.build.sha == SHA


# ── план ────────────────────────────────────────────────────────────────────
@pytest.fixture
def planned(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(runtime_service, "resolve_repo", lambda **kw: tmp_path / "repo")
    monkeypatch.setattr(runtime_service, "default_branch", lambda repo: "origin/main")
    monkeypatch.setattr(runtime_service, "resolve_ref", lambda repo, ref: SHA)
    monkeypatch.setattr(
        runtime_service,
        "installed_versions",
        lambda *, runtime, repo: [
            runtime_service.InstallVersion(
                name="рантайм сервисов",
                binary=str(runtime / "bin" / "cod-doc"),
                version="1.4.1",
            )
        ],
    )


def test_plan_resolves_revision_and_current_version(tmp_path, cfg, calls, db, planned):
    update_plan = update_service.plan(
        cfg, ref=None, runtime=tmp_path / "runtime", repo=None, projects=None
    )
    assert update_plan.ref == "origin/main"
    assert update_plan.target_sha == SHA
    assert update_plan.current_version == "1.4.1"
    assert update_plan.projects == PROJECTS
    assert [p.project for p in update_plan.repair] == PROJECTS
    assert calls.diagnose == PROJECTS


def test_plan_marks_projects_sharing_a_hub_db(tmp_path, calls, db, planned):
    """Миграция hub-БД задевает всех её потребителей — человек должен это видеть."""
    hub = "postgresql://localhost/cod_doc"
    cfg = Config(
        projects=[
            ProjectEntry(name="alpha", path=str(tmp_path / "a"), db_url=hub).model_dump(),
            ProjectEntry(name="beta", path=str(tmp_path / "b"), db_url=hub).model_dump(),
            ProjectEntry(name="lonely", path=str(tmp_path / "c")).model_dump(),
        ]
    )
    update_plan = update_service.plan(
        cfg, ref=None, runtime=tmp_path / "runtime", repo=None, projects=None
    )
    assert update_plan.hub_projects == ["alpha", "beta"]


def test_plan_with_skip_install_does_not_touch_git(tmp_path, cfg, calls, db, monkeypatch):
    """`--skip-install` — это в том числе «не ходи в сеть за ревизией»."""
    monkeypatch.setattr(
        runtime_service,
        "resolve_repo",
        lambda **kw: pytest.fail("resolve_repo при --skip-install"),
    )
    monkeypatch.setattr(runtime_service, "installed_versions", lambda *, runtime, repo: [])
    update_plan = update_service.plan(
        cfg,
        ref=None,
        runtime=tmp_path / "runtime",
        repo=None,
        projects=None,
        skip=SkipFlags(install=True),
    )
    assert update_plan.target_sha is None


def test_has_irreversible_names_what_needs_confirmation(tmp_path):
    """Свап откатывается, `uv tool install` и импорт документа — нет."""
    assert _plan(tmp_path).has_irreversible is True

    quiet = _plan(tmp_path, skip=SkipFlags(install=True, migrate=True, repair=True))
    assert quiet.has_irreversible is False

    with_import = _plan(tmp_path, skip=SkipFlags(install=True, migrate=True))
    with_import.repair = [
        repair_service.RepairPlan(
            project="alpha",
            actions=[
                repair_service.RepairAction(kind=repair_service.KIND_DOC_IMPORT, ref="MASTER.md")
            ],
        )
    ]
    assert with_import.has_irreversible is True
    assert json.dumps(with_import.as_dict())


def test_dry_run_changes_nothing(tmp_path, cfg, calls, db):
    report = update_service.run(cfg, update_plan=_plan(tmp_path), dry_run=True)
    assert report.dry_run is True
    assert all(phase.skipped and phase.skip_reason for phase in report.phases)
    assert calls.order == []


# ── замок ───────────────────────────────────────────────────────────────────
def test_lock_refuses_a_second_run_by_name(tmp_path):
    """Два `update` разом — это два alembic на одной БД и два `mv` рантайма."""
    lock = tmp_path / "update.lock"
    with update_service.update_lock(lock) as held:
        assert held == lock
        with pytest.raises(update_service.UpdateLocked) as caught, update_service.update_lock(lock):
            pytest.fail("замок пустил второй прогон")
    assert str(lock) in str(caught.value)
    # Освободился — следующий прогон проходит.
    with update_service.update_lock(lock):
        pass


def test_run_takes_the_lock(tmp_path, cfg, calls, db, monkeypatch):
    """Прогон обязан держать замок: `routine tick` не должен вклиниться."""
    held: list[str] = []

    @contextmanager
    def spy(path=None):
        held.append("locked")
        yield tmp_path / "update.lock"

    monkeypatch.setattr(update_service, "update_lock", spy)
    update_service.run(cfg, update_plan=_plan(tmp_path, skip=SkipFlags(install=True)))
    assert held == ["locked"]
