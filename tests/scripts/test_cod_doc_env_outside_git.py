"""ADO-146: `cod-doc-env.sh` не должен умирать молча.

Скрипт `source`-ится лаунчерами, у которых стоит `set -euo pipefail`. Любая
подстановка команды без `|| true` в такой оболочке убивает процесс до `exec`:
`2>/dev/null` прячет текст ошибки, но не код возврата, а `pipefail`
протаскивает его наружу из середины конвейера. Наблюдаемый симптом у клиента —
`CONNECTION_CLOSED` без единой строки в логе: Python не успевал запуститься.

Два живых источника такого кода возврата:

* `git worktree list` вне git-репозитория → 128;
* `sqlite3 -readonly` на отсутствующей, заблокированной или WAL-базе без прав
  создать `-shm` → ненуль.

Тест гоняет настоящий bash, а не имитацию: дефект существует только под
`set -e` + `pipefail`, и на любой более мягкой оболочке воспроизвести его
нельзя.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "plugins" / "cod-doc" / "scripts" / "cod-doc-env.sh"

#: Ровно та оболочка, что у вызывающих скрипт лаунчеров.
_STRICT = "set -euo pipefail"


def _source_under_strict_shell(
    workdir: Path, *, project_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Заsource-ить скрипт в строгой оболочке и вернуть результат целиком."""
    target = project_dir if project_dir is not None else workdir
    body = f'{_STRICT}\nsource "{SCRIPT}"\necho "SURVIVED slug=${{COD_DOC_SLUG:-}}"\n'
    return subprocess.run(
        ["bash", "-c", body],
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(target)},
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def outside_any_repo(tmp_path: Path) -> Path:
    """Каталог, заведомо не принадлежащий ни одному git-репозиторию.

    `tmp_path` для этого годится не всегда: если TMPDIR окажется внутри
    репозитория (а домашний каталог этого проекта — git-репозиторий), то
    `git worktree list` отработает успешно и дефект не проявится.
    """
    if shutil.which("git") is None:
        pytest.skip("git не установлен — воспроизводить нечего")
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        pytest.skip(f"tmp_path лежит внутри репозитория {probe.stdout.strip()}")
    return tmp_path


def test_survives_outside_git_repository(outside_any_repo: Path) -> None:
    """Главный кейс ADO-146: вне репозитория скрипт обязан дожить до конца."""
    result = _source_under_strict_shell(outside_any_repo)

    assert result.returncode == 0, (
        f"скрипт умер с кодом {result.returncode}; stderr: {result.stderr!r}. "
        "Похоже, вернулась неохраняемая подстановка команды."
    )
    assert "SURVIVED" in result.stdout


def test_survives_when_state_db_is_unreadable(outside_any_repo: Path) -> None:
    """Второй источник ненулевого кода: sqlite3 на нечитаемой БД.

    `COD_DOC_ROOT` передаётся явно, чтобы дойти до ветки со слагом, минуя
    git-ветку; `state.db` — не база, а мусор, на котором sqlite3 выходит ненулём.
    """
    root = outside_any_repo / "proj"
    (root / ".cod-doc").mkdir(parents=True)
    (root / ".cod-doc" / "state.db").write_text("это не база данных", encoding="utf-8")

    body = f'{_STRICT}\nexport COD_DOC_ROOT="{root}"\nsource "{SCRIPT}"\necho "SURVIVED slug=${{COD_DOC_SLUG:-}}"\n'
    result = subprocess.run(
        ["bash", "-c", body],
        cwd=root,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(root)},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"скрипт умер с кодом {result.returncode}; stderr: {result.stderr!r}"
    )
    assert "SURVIVED" in result.stdout


def test_exports_the_three_variables(outside_any_repo: Path) -> None:
    """Контракт скрипта: три переменные экспортированы, пусть и пустыми."""
    body = (
        f"{_STRICT}\n"
        f'source "{SCRIPT}"\n'
        "for v in COD_DOC_BIN COD_DOC_ROOT COD_DOC_SLUG; do\n"
        '  declare -p "$v" >/dev/null || { echo "missing $v" >&2; exit 1; }\n'
        "done\n"
        "echo OK\n"
    )
    result = subprocess.run(
        ["bash", "-c", body],
        cwd=outside_any_repo,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(outside_any_repo)},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


#: Подстановки, которые не могут вернуть ненуль. Ратчет: список может только
#: уменьшаться, и каждая запись обязана объяснять, почему падение невозможно.
#: `|| true` там был бы не страховкой, а маскировкой — он спрятал бы поломку,
#: которая по построению означала бы сломанный инвариант, а не внешний сбой.
_CANNOT_FAIL: dict[str, str] = {
    'd="$(dirname "$d")"': (
        "dirname на непустой строке не падает; вход — результат предыдущей "
        "итерации того же цикла, пустым он стать не может"
    ),
    'COD_DOC_BIN="$(command -v cod-doc)"': (
        "строкой выше стоит `elif command -v cod-doc >/dev/null 2>&1`, то есть "
        "ветка исполняется только когда та же команда уже вернула ноль"
    ),
}


def test_every_command_substitution_is_guarded() -> None:
    """Анти-дрейф: новая подстановка без `|| true` вернёт тот же дефект.

    Проверка по исходнику, а не по поведению: воспроизвести падение можно лишь
    для тех веток, куда тест сумел зайти, а строк с подстановками в скрипте
    больше, чем достижимых в тесте состояний окружения.
    """
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()

    offenders: list[str] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#") or '="$(' not in stripped:
            continue
        # Подстановка может занимать несколько строк — ищем закрывающую.
        chunk = stripped
        j = i
        while not chunk.rstrip().endswith('"') or chunk.rstrip().endswith("\\"):
            if j >= len(lines):
                break
            chunk += " " + lines[j].strip()
            j += 1
        if "|| true" in chunk or stripped in _CANNOT_FAIL:
            continue
        offenders.append(f"{SCRIPT.name}:{i}: {stripped}")

    assert not offenders, (
        "подстановка команды без `|| true` — под `set -euo pipefail` у "
        "вызывающего она убьёт скрипт молча (ADO-146):\n  " + "\n  ".join(offenders)
    )


def test_cannot_fail_allowlist_is_not_stale() -> None:
    """Ратчет не должен протухать: снятое исключение обязано исчезнуть."""
    source = SCRIPT.read_text(encoding="utf-8")
    stale = [line for line in _CANNOT_FAIL if line not in source]
    assert not stale, (
        "в _CANNOT_FAIL остались строки, которых больше нет в скрипте:\n  " + "\n  ".join(stale)
    )
