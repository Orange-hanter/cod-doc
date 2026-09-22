"""ADO-212: pytest из git-хука worktree не должен трогать настоящий репозиторий.

При push из worktree git экспортирует в окружение хука ``GIT_DIR`` —
абсолютный путь ``.git/worktrees/<имя>`` (из основного чекаута не задаёт).
``hooks/pre-push`` зовёт ``scripts/gate.sh``, тот — pytest, и переменная
доезжает до тестов. Фикстуры вида ``git -C <tmp_path> init/add -A/commit``
при заданном ``GIT_DIR`` работают не со своим ``<tmp_path>/.git``, а с
репозиторием из переменной:

* ``git init`` переинициализирует его и, раз путь не кончается на ``/.git``,
  пишет ``core.bare=true`` в общий конфиг — основной чекаут перестаёт видеть
  рабочее дерево;
* ``add -A`` + ``commit`` кладут на ветку worktree коммит, дерево которого —
  только файлы фикстуры: все настоящие файлы в нём удалены.

Хук при красном гейте советовал ``SKIP_GATE=1 git push`` — и этот совет
выталкивал наружу коммит, удаляющий репозиторий (закрытый PR #96).

Тест воспроизводит это на одноразовом репозитории: гоняет настоящий тест с
git-фикстурой во вложенном pytest с ``GIT_DIR`` на worktree и проверяет, что ни
ветка, ни ``core.bare`` не изменились. Вложенный процесс здесь не имитация:
дефект живёт в окружении процесса, и поймать его можно только в нём.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from tests.conftest import _GIT_REPO_LOCATORS

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "gate.sh"

#: `unset GIT_… \` с переносами строк — ровно одна такая инструкция в раннере.
_UNSET_GIT = re.compile(r"^unset (GIT_[A-Z_]+(?:\s+(?:\\\n\s*)?GIT_[A-Z_]+)*)", re.MULTILINE)

#: Настоящий тест с фикстурой ``git -C <tmp_path> init/add/commit``.
_VICTIM = "tests/services/test_commit_link_service.py::test_import_idempotent"

#: Вложенный pytest поднимает схему через alembic — секунды, не минуты.
_NESTED_TIMEOUT_S = 300


def _env_without_git() -> dict[str, str]:
    """Окружение без ``GIT_*``: сама подготовка не должна наследовать дефект."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_env_without_git(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo_with_worktree(root: Path) -> tuple[Path, Path, Path]:
    """Одноразовый репозиторий с настоящим файлом и worktree на ветке ``topic``."""
    repo = root / "real"
    _git("init", "-q", "--initial-branch=main", str(repo), cwd=root)
    _git("config", "user.email", "real@example.com", cwd=repo)
    _git("config", "user.name", "real", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "important.txt").write_text("keep\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "real: important file", cwd=repo)
    worktree = root / "wt"
    _git("worktree", "add", "-q", "-b", "topic", str(worktree), cwd=repo)
    gitdir = Path(_git("rev-parse", "--absolute-git-dir", cwd=worktree))
    return repo, worktree, gitdir


def test_pytest_under_worktree_git_dir_leaves_the_repository_alone(tmp_path: Path) -> None:
    repo, worktree, gitdir = _repo_with_worktree(tmp_path)
    head_before = _git("rev-parse", "HEAD", cwd=worktree)

    # Ровно то окружение, которое git отдаёт pre-push при push из worktree.
    env = {**_env_without_git(), "GIT_DIR": str(gitdir)}
    nested = subprocess.run(
        [sys.executable, "-m", "pytest", _VICTIM, "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=_NESTED_TIMEOUT_S,
        check=False,
    )

    assert _git("rev-parse", "HEAD", cwd=worktree) == head_before, (
        "фикстура закоммитила в ветку worktree: " + _git("log", "--oneline", "-3", cwd=worktree)
    )
    assert _git("config", "--local", "core.bare", cwd=repo) == "false"
    assert (worktree / "important.txt").exists()
    assert nested.returncode == 0, nested.stdout[-2000:] + nested.stderr[-2000:]


def test_gate_runner_and_conftest_reset_the_same_git_variables() -> None:
    """Набор продублирован в bash и Python — пусть расхождение роняет CI, а не молчит.

    Раннер защищает прогон из хука, conftest — любой запуск pytest; если один
    из списков пополнят, а другой нет, защита станет дырявой ровно с одной
    стороны, и заметить это можно будет только новым инцидентом.
    """
    match = _UNSET_GIT.search(GATE.read_text(encoding="utf-8"))
    assert match is not None, "в scripts/gate.sh нет `unset GIT_…`"
    in_gate = set(re.findall(r"GIT_[A-Z_]+", match.group(1)))
    assert in_gate == set(_GIT_REPO_LOCATORS)
