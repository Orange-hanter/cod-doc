"""ADO-192: сборка рантайма и атомарный свап (`cod_doc/services/runtime_service.py`).

Внешние бинари (`git`, `uv`, console-script'ы рантайма) подменяются в одной
точке — `runtime_service._run`; проверяется именно argv, потому что цена почти
каждой механики здесь — один флаг: пропавший `--relocatable` роняет демоны с
`bad interpreter` (код 78), пропавший `-P` заставляет smoke-тест импортировать
локальное рабочее дерево вместо собранного пакета.

Свап и откат гоняются по-настоящему на `tmp_path`: это переименования каталогов,
и мокать в них нечего — мок проверял бы только сам себя.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
from pathlib import Path

import pytest

from cod_doc.services import runtime_service

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


# ── дублёр внешних бинарей ──────────────────────────────────────────────────
class _FakeRun:
    """Подмена `runtime_service._run`: пишет argv, отдаёт заготовленные ответы.

    Совпадение ищется подстрокой в склеенном argv — так тест говорит «вызов
    uv venv», а не повторяет полный список аргументов, который он же и проверяет.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._stdout: list[tuple[str, str]] = []
        self._codes: list[tuple[str, int]] = []
        self._effects: list[tuple[str, object]] = []

    def stdout_for(self, needle: str, text: str) -> _FakeRun:
        self._stdout.append((needle, text))
        return self

    def fail(self, needle: str, code: int = 1) -> _FakeRun:
        self._codes.append((needle, code))
        return self

    def on(self, needle: str, effect) -> _FakeRun:
        self._effects.append((needle, effect))
        return self

    def __call__(self, argv, *, cwd=None, check=True, timeout=None):
        self.calls.append(list(argv))
        line = " ".join(argv)
        for needle, effect in self._effects:
            if needle in line:
                effect(list(argv))
        code = next((c for needle, c in self._codes if needle in line), 0)
        out = next((text for needle, text in self._stdout if needle in line), "")
        if code and check:
            raise runtime_service.RuntimeError_(f"fake: `{line}` вернула {code}")
        return subprocess.CompletedProcess(list(argv), code, out, "")

    def argv(self, *needles: str) -> list[str]:
        """Первый вызов, чей argv содержит все подстроки; иначе — внятный провал."""
        for argv in self.calls:
            line = " ".join(argv)
            if all(needle in line for needle in needles):
                return argv
        printed = "\n".join("  " + " ".join(c) for c in self.calls)
        raise AssertionError(f"нет вызова {needles!r}. Были:\n{printed}")

    def called(self, *needles: str) -> bool:
        return any(all(n in " ".join(argv) for n in needles) for argv in self.calls)


class _FakeMetadata:
    """Минимум от `PackageMetadata`: нас интересуют только строки `Project-URL`."""

    def __init__(self, entries: list[str]) -> None:
        self._entries = entries

    def get_all(self, name: str, failobj: object = None) -> object:
        return self._entries if name == "Project-URL" else failobj


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeRun:
    runner = _FakeRun()
    monkeypatch.setattr(runtime_service, "_run", runner)
    return runner


@pytest.fixture
def dist_metadata(monkeypatch: pytest.MonkeyPatch):
    """Подменяет метаданные дистрибутива; `None` — «пакет не установлен».

    Настоящие метаданные сюда пускать нельзя: они пишутся при сборке колеса, и
    у editable-венва в чекауте `[project.urls]` может не быть — тест про
    фолбэк проходил бы или падал в зависимости от того, когда переставляли
    пакет.
    """

    def _install(entries: list[str] | None) -> None:
        def _metadata(name: str) -> _FakeMetadata:
            if entries is None:
                raise importlib.metadata.PackageNotFoundError(name)
            return _FakeMetadata(entries)

        monkeypatch.setattr(importlib.metadata, "metadata", _metadata)

    return _install


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`installed_versions` смотрит в ~/.local/bin — настоящий HOME сюда не пускаем."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _tree(root: Path, marker: str) -> Path:
    """Дерево рантайма с опознавательным маркером и тремя console-script'ами."""
    (root / "bin").mkdir(parents=True)
    (root / "marker.txt").write_text(marker, encoding="utf-8")
    for name in ("python", "cod-doc", "cod-doc-mcp"):
        binary = root / "bin" / name
        binary.write_text(f"#!/bin/sh\n# {marker}\n", encoding="utf-8")
        binary.chmod(0o755)
    return root


def _marker(root: Path) -> str:
    return (root / "marker.txt").read_text(encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _build_succeeds(fake: _FakeRun, staged: Path, *, version: str = "1.5.0") -> None:
    fake.on("uv venv", lambda argv: _tree(staged, "new"))
    fake.stdout_for(runtime_service._VERSION_PROBE, version)


# ── _run: единственная точка запуска внешних бинарей ────────────────────────
def test_run_names_the_missing_binary_and_how_to_get_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(runtime_service.RuntimeError_, match="brew install uv"):
        runtime_service._run(["uv", "--version"])


def test_run_quotes_stderr_of_a_failed_command() -> None:
    with pytest.raises(runtime_service.RuntimeError_, match="boom"):
        runtime_service._run(["/bin/sh", "-c", "echo boom >&2; exit 3"])


# ── resolve_ref: катится только смерженное ──────────────────────────────────
def test_resolve_ref_rejects_commit_outside_default_branch(tmp_path: Path, fake: _FakeRun) -> None:
    repo = _repo(tmp_path)
    fake.stdout_for("rev-parse --verify", SHA)
    fake.stdout_for("rev-parse --abbrev-ref", "origin/trunk")
    fake.fail("merge-base --is-ancestor")

    with pytest.raises(runtime_service.RuntimeError_, match="не является предком origin/trunk"):
        runtime_service.resolve_ref(repo, "feature/local")

    # Проверка обязана быть настоящей: сам вызов merge-base, с sha и веткой.
    argv = fake.argv("merge-base", "--is-ancestor")
    assert argv[-2:] == [SHA, "origin/trunk"]


def test_resolve_ref_fetches_tags_before_deciding(tmp_path: Path, fake: _FakeRun) -> None:
    repo = _repo(tmp_path)
    fake.stdout_for("rev-parse --verify", SHA)
    fake.stdout_for("rev-parse --abbrev-ref", "origin/main")

    assert runtime_service.resolve_ref(repo, "origin/main") == SHA

    fetch = fake.argv("fetch", "origin")
    assert "--tags" in fetch
    assert "--prune" in fetch
    assert fake.calls.index(fetch) < fake.calls.index(fake.argv("merge-base"))


def test_default_branch_falls_back_when_remote_head_is_unknown(
    tmp_path: Path, fake: _FakeRun
) -> None:
    fake.fail("rev-parse --abbrev-ref")
    assert runtime_service.default_branch(_repo(tmp_path)) == "origin/main"


# ── build_staged ────────────────────────────────────────────────────────────
def test_build_creates_relocatable_venv(tmp_path: Path, fake: _FakeRun) -> None:
    """Пропавший `--relocatable` — это `bad interpreter` (78) на всех демонах."""
    runtime = tmp_path / "runtime"
    _build_succeeds(fake, tmp_path / "runtime.staged")

    runtime_service.build_staged(_repo(tmp_path), SHA, runtime=runtime)

    argv = fake.argv("uv venv")
    assert "--relocatable" in argv
    assert str(tmp_path / "runtime.staged") in argv
    assert argv[argv.index("--python") + 1] == runtime_service.DEFAULT_PYTHON_VERSION


def test_build_exports_revision_as_a_worktree_not_an_archive(
    tmp_path: Path, fake: _FakeRun
) -> None:
    """`git archive` оставил бы дерево без .git — setuptools-scm не вывел бы версию."""
    _build_succeeds(fake, tmp_path / "runtime.staged")

    report = runtime_service.build_staged(_repo(tmp_path), SHA, runtime=tmp_path / "runtime")

    argv = fake.argv("worktree", "add", "--detach")
    assert argv[-2:] == [report.src, SHA]
    assert not fake.called("archive")


def test_build_smoke_keeps_cwd_out_of_sys_path(tmp_path: Path, fake: _FakeRun) -> None:
    """Без `-P` импорт берёт локальное рабочее дерево, и проверка врёт."""
    staged = tmp_path / "runtime.staged"
    _build_succeeds(fake, staged)

    runtime_service.build_staged(_repo(tmp_path), SHA, runtime=tmp_path / "runtime")

    argv = fake.argv("import cod_doc")
    assert argv[0] == str(staged / "bin" / "python")
    assert argv.index("-P") < argv.index("-c")


def test_build_installs_into_the_freshly_made_venv(tmp_path: Path, fake: _FakeRun) -> None:
    """Установка «поверх» не удаляет модули, исчезнувшие из пакета."""
    staged = tmp_path / "runtime.staged"
    _build_succeeds(fake, staged)

    report = runtime_service.build_staged(_repo(tmp_path), SHA, runtime=tmp_path / "runtime")

    argv = fake.argv("uv pip install")
    assert argv[argv.index("--python") + 1] == str(staged / "bin" / "python")
    assert report.src in argv
    assert fake.calls.index(fake.argv("uv venv")) < fake.calls.index(argv)


def test_build_reports_version_and_runs_the_console_script(tmp_path: Path, fake: _FakeRun) -> None:
    staged = tmp_path / "runtime.staged"
    _build_succeeds(fake, staged, version="1.5.0")

    report = runtime_service.build_staged(_repo(tmp_path), SHA, runtime=tmp_path / "runtime")

    assert report.ok is True
    assert report.version == "1.5.0"
    assert report.sha == SHA
    assert report.staged == str(staged)
    assert fake.argv("bin/cod-doc --help")[0] == str(staged / "bin" / "cod-doc")


def test_build_failure_comes_back_as_a_report(tmp_path: Path, fake: _FakeRun) -> None:
    """Отчёт, а не исключение: вызывающему нужен `src`, чтобы снять worktree."""
    _build_succeeds(fake, tmp_path / "runtime.staged")
    fake.fail("uv pip install")

    report = runtime_service.build_staged(_repo(tmp_path), SHA, runtime=tmp_path / "runtime")

    assert report.ok is False
    assert report.error is not None
    assert "uv pip install" in report.error
    assert report.src


# ── drop_build_src ──────────────────────────────────────────────────────────
def test_drop_build_src_removes_the_tree_when_git_refuses(tmp_path: Path, fake: _FakeRun) -> None:
    parent = tmp_path / "tmpbuild"
    src = parent / "src"
    src.mkdir(parents=True)
    (src / "file.txt").write_text("x", encoding="utf-8")
    fake.fail("worktree remove", code=128)

    runtime_service.drop_build_src(src)

    assert not src.exists()
    assert not parent.exists()


def test_install_runtime_drops_build_src_even_when_build_fails(
    tmp_path: Path, fake: _FakeRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Временный worktree снимается через `finally`, а не только на счастливом пути."""
    dropped: list[Path] = []
    monkeypatch.setattr(runtime_service, "drop_build_src", dropped.append)
    _build_succeeds(fake, tmp_path / "runtime.staged")
    fake.fail("uv pip install")

    report = runtime_service.install_runtime(_repo(tmp_path), SHA, runtime=tmp_path / "runtime")

    assert report.ok is False
    assert report.build is not None
    assert dropped == [Path(report.build.src)]


# ── swap / rollback: без моков, настоящие переименования ────────────────────
def test_swap_moves_the_running_tree_aside(tmp_path: Path) -> None:
    runtime = _tree(tmp_path / "runtime", "old")
    _tree(tmp_path / "runtime.staged", "new")

    report = runtime_service.swap(runtime)

    assert report.ok is True
    assert _marker(runtime) == "new"
    assert report.previous == str(tmp_path / "runtime.previous")
    assert _marker(tmp_path / "runtime.previous") == "old"
    assert not (tmp_path / "runtime.staged").exists()


def test_swap_without_a_staged_build_is_refused(tmp_path: Path) -> None:
    runtime = _tree(tmp_path / "runtime", "old")

    report = runtime_service.swap(runtime)

    assert report.ok is False
    assert "runtime.staged" in (report.error or "")
    assert _marker(runtime) == "old"


def test_rollback_is_reversible(tmp_path: Path) -> None:
    """Повторный откат возвращает ровно исходное состояние."""
    runtime = _tree(tmp_path / "runtime", "new")
    _tree(tmp_path / "runtime.previous", "old")

    first = runtime_service.rollback(runtime)
    assert first.rolled_back is True
    assert _marker(runtime) == "old"
    assert _marker(tmp_path / "runtime.previous") == "new"

    runtime_service.rollback(runtime)
    assert _marker(runtime) == "new"
    assert _marker(tmp_path / "runtime.previous") == "old"
    assert not (tmp_path / "runtime.rollback-tmp").exists()


def test_rollback_without_previous_is_refused(tmp_path: Path) -> None:
    runtime = _tree(tmp_path / "runtime", "new")

    report = runtime_service.rollback(runtime)

    assert report.ok is False
    assert report.rolled_back is False
    assert "нечего откатывать" in (report.error or "")
    assert _marker(runtime) == "new"


def test_verify_swapped_runs_the_mcp_console_script(tmp_path: Path, fake: _FakeRun) -> None:
    runtime = _tree(tmp_path / "runtime", "new")

    ok, detail = runtime_service.verify_swapped(runtime)

    assert (ok, detail) == (True, "")
    assert fake.argv("cod-doc-mcp --help")[0] == str(runtime / "bin" / "cod-doc-mcp")


def test_install_runtime_rolls_back_when_swapped_runtime_is_broken(
    tmp_path: Path, fake: _FakeRun
) -> None:
    """Smoke-тест до свапа проходит, а по конечному пути — нет: откат без человека."""
    runtime = _tree(tmp_path / "runtime", "old")
    _build_succeeds(fake, tmp_path / "runtime.staged")
    fake.fail("cod-doc-mcp --help", code=78)

    report = runtime_service.install_runtime(_repo(tmp_path), SHA, runtime=runtime)

    assert report.ok is False
    assert report.swap is not None
    assert report.swap.rolled_back is True
    assert _marker(runtime) == "old", "по рабочему пути обязан вернуться прежний рантайм"
    assert _marker(tmp_path / "runtime.broken") == "new", "сломанная сборка сохраняется"
    assert report.swap.broken == str(tmp_path / "runtime.broken")
    assert not (tmp_path / "runtime.previous").exists()


def test_install_runtime_keeps_the_new_tree_when_it_works(tmp_path: Path, fake: _FakeRun) -> None:
    runtime = _tree(tmp_path / "runtime", "old")
    _build_succeeds(fake, tmp_path / "runtime.staged")

    report = runtime_service.install_runtime(_repo(tmp_path), SHA, runtime=runtime)

    assert report.ok is True
    assert _marker(runtime) == "new"
    assert _marker(tmp_path / "runtime.previous") == "old"
    assert not (tmp_path / "runtime.broken").exists()
    assert [v.name for v in report.versions] == [
        "рантайм сервисов",
        "PATH (uv tool)",
        "репозиторий (editable)",
    ]


# ── версии установок ────────────────────────────────────────────────────────
def test_installed_versions_reports_a_missing_install(tmp_path: Path, fake: _FakeRun) -> None:
    versions = runtime_service.installed_versions(runtime=tmp_path / "runtime", repo=None)

    by_name = {v.name: v for v in versions}
    assert by_name["рантайм сервисов"].version is None
    assert "не установлен" in (by_name["рантайм сервисов"].error or "")
    assert not fake.calls, "несуществующий бинарь не запускаем"


def test_installed_versions_falls_back_to_the_interpreter_from_shebang(
    tmp_path: Path, fake: _FakeRun
) -> None:
    """`--version` появился только в ADO-189: на старой сборке спрашиваем импортом."""
    runtime = tmp_path / "runtime"
    interpreter = tmp_path / "py"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o755)
    (runtime / "bin").mkdir(parents=True)
    binary = runtime / "bin" / "cod-doc"
    binary.write_text(f"#!{interpreter}\n", encoding="utf-8")
    binary.chmod(0o755)
    fake.fail("--version", code=2)
    fake.stdout_for("import cod_doc", "cod-doc, version 1.2.3")

    versions = runtime_service.installed_versions(runtime=runtime, repo=None)

    assert versions[0].version == "cod-doc, version 1.2.3"
    probe = fake.argv("import cod_doc")
    assert probe[0] == str(interpreter)
    assert "-P" in probe


def test_sync_path_tool_reinstalls_by_force(tmp_path: Path, fake: _FakeRun) -> None:
    src = tmp_path / "src"
    src.mkdir()

    assert runtime_service.sync_path_tool(src) == runtime_service.UNKNOWN_VERSION

    argv = fake.argv("uv tool install")
    assert "--force" in argv
    assert argv[argv.index("--from") + 1] == str(src)
    assert "cod-doc" in argv


# ── resolve_repo ────────────────────────────────────────────────────────────
def test_resolve_repo_takes_the_local_checkout_from_env(
    tmp_path: Path, fake: _FakeRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("COD_DOC_REPO", str(repo))

    resolved = runtime_service.resolve_repo(repo=None, src_cache=tmp_path / "cache")

    assert resolved == repo
    assert not fake.calls, "локальный чекаут работает офлайн, клонировать нечего"


def test_resolve_repo_rejects_an_explicit_path_that_is_not_a_repo(tmp_path: Path) -> None:
    with pytest.raises(runtime_service.RuntimeError_, match="не репозиторий"):
        runtime_service.resolve_repo(repo=tmp_path / "nowhere", src_cache=tmp_path / "cache")


def test_resolve_repo_clones_when_there_is_nothing_local(
    tmp_path: Path, fake: _FakeRun, monkeypatch: pytest.MonkeyPatch, dist_metadata
) -> None:
    """Окружение сильнее метаданных: форк и зеркало задаются именно так."""
    monkeypatch.setenv("COD_DOC_REPO_URL", "https://example.invalid/cod-doc.git")
    dist_metadata(["Repository, https://github.com/Orange-hanter/cod-doc"])
    cache = tmp_path / "cache" / "src"

    resolved = runtime_service.resolve_repo(repo=None, src_cache=cache)

    assert resolved == cache
    argv = fake.argv("git clone")
    assert "--filter=blob:none" in argv
    assert argv[-2:] == ["https://example.invalid/cod-doc.git", str(cache)]


def test_resolve_repo_clones_the_url_from_distribution_metadata(
    tmp_path: Path, fake: _FakeRun, dist_metadata
) -> None:
    """Голая установка без чекаута: адрес берётся из `[project.urls]`."""
    dist_metadata(
        [
            "Documentation, https://example.invalid/docs",
            "Repository, https://github.com/Orange-hanter/cod-doc",
        ]
    )
    cache = tmp_path / "cache" / "src"

    resolved = runtime_service.resolve_repo(repo=None, src_cache=cache)

    assert resolved == cache
    assert fake.argv("git clone")[-2:] == [
        "https://github.com/Orange-hanter/cod-doc",
        str(cache),
    ]


def test_resolve_repo_without_a_url_says_what_to_do(
    tmp_path: Path, fake: _FakeRun, dist_metadata
) -> None:
    """Пакет не установлен — метаданных нет вовсе, и это не стек, а подсказка."""
    dist_metadata(None)

    with pytest.raises(runtime_service.RuntimeError_, match="--skip-install"):
        runtime_service.resolve_repo(repo=None, src_cache=tmp_path / "cache")
    assert not fake.calls


def test_resolve_repo_without_a_repository_label_says_what_to_do(
    tmp_path: Path, fake: _FakeRun, dist_metadata
) -> None:
    """Метаданные есть, но про репозиторий молчат — клонировать по-прежнему нечего."""
    dist_metadata(["Documentation, https://example.invalid/docs", "Broken entry without comma"])

    with pytest.raises(runtime_service.RuntimeError_, match="--skip-install"):
        runtime_service.resolve_repo(repo=None, src_cache=tmp_path / "cache")
    assert not fake.calls
