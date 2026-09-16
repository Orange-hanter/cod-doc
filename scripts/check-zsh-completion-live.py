#!/usr/bin/env python3
"""Живая проверка zsh-дополнения: настоящий TAB в настоящем интерактивном zsh.

Зачем отдельный скрипт, когда есть `tests/cli/test_zsh_completion_*.py`:

    Guard `(( $+functions[_cod-doc] ))` на КОРНЕВОЙ функции даёт бесконечную
    рекурсию при автозагрузке — тело файла #compdef становится телом
    функции-заглушки, guard пропускает определение, а завершающий
    `_cod-doc "$@"` зовёт заглушку снова. `zsh -n` такой файл разбирает без
    единой претензии, юнит-тесты тоже. Видно это ТОЛЬКО по настоящему TAB.

Скрипт не входит в CI: нужен pty, а на runner'е это лишняя флакующая
зависимость. Прогоняй руками после правок `prelude.zsh` или формы спек:

    .venv/bin/python scripts/check-zsh-completion-live.py

Окружение пользователя не трогается: свой ZDOTDIR, свой fpath, свой
zcompdump во временном каталоге. Код возврата 1 — что-то сломалось.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import pty
import re
import select
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "cod_doc" / "cli" / "completion" / "_cod-doc"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[=>]|\r")
_NOISE = re.compile(
    r"Error|error:|not found|no such|parse error|maximum nested|bad pattern|"
    r"unknown file attribute|bad math|\.zsh:\d+|_cod-doc:\d+",
    re.IGNORECASE,
)
#: Как выглядит настоящий кандидат в выводе: либо строка `значение -- описание`
#: от `_describe`, либо вопрос zsh про длинный список.
_CANDIDATE = re.compile(r"\S+\s+--\s+\S|do you wish to see all \d+ possibilities")

_PROMPT = "READY> "
_BOOT_TIMEOUT = 8.0
_SETTLE = 4.0

#: Проект, на котором гоняем «должно что-то найтись». Слаг обязан быть в
#: ~/.cod-doc/config.yaml, иначе кейсы с данными честно скажут «пусто».
LIVE_PROJECT = "cod-doc"


def _spawn(zdotdir: str, cwd: str, env_extra: dict[str, str]) -> tuple[int, int]:
    env = dict(os.environ)
    env["ZDOTDIR"] = zdotdir
    env["PATH"] = f"{REPO / '.venv' / 'bin'}:{env['PATH']}"
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "200"
    env["LINES"] = "60"
    env.update(env_extra)

    pid, fd = pty.fork()
    if pid == 0:  # потомок: это уже отдельный процесс, os._exit не нужен
        os.chdir(cwd)
        os.execvpe("zsh", ["zsh", "-i"], env)
    return pid, fd


def _drain(fd: int, seconds: float, stop_on: str | None = None) -> str:
    chunks: list[str] = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.3)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536).decode("utf-8", "replace")
        except OSError:
            break
        chunks.append(chunk)
        if stop_on and stop_on in chunk:
            break
    return "".join(chunks)


def run_case(zdotdir: str, line: str, cwd: str, env_extra: dict[str, str]) -> str:
    """Набрать `line`, нажать TAB, вернуть вывод без ANSI."""
    pid, fd = _spawn(zdotdir, cwd, env_extra)
    _drain(fd, _BOOT_TIMEOUT, stop_on=_PROMPT)

    os.write(fd, line.encode())
    time.sleep(0.3)
    os.write(fd, b"\t")
    body = _drain(fd, _SETTLE)

    os.write(fd, b"\x03")  # Ctrl-C: снять набранное
    time.sleep(0.2)
    os.write(fd, b"exit\n")
    with contextlib.suppress(OSError):
        os.close(fd)
    os.waitpid(pid, 0)
    return _ANSI.sub("", body).replace(line, "", 1)


def _make_zdotdir(tmp: pathlib.Path) -> pathlib.Path:
    comp = tmp / "completions"
    comp.mkdir()
    (comp / "_cod-doc").symlink_to(ARTIFACT)
    (tmp / ".zshrc").write_text(
        f"fpath=({comp} $fpath)\n"
        "autoload -Uz compinit\n"
        f"compinit -u -d {tmp}/zcompdump\n"
        "zstyle ':completion:*' menu no\n"
        "unsetopt ALWAYS_TO_END AUTO_MENU\n"
        "setopt NO_BEEP\n"
        f"PS1='{_PROMPT}'\n",
        encoding="utf-8",
    )
    return tmp


def _lean_bin(tmp: pathlib.Path) -> pathlib.Path:
    """PATH со всем нужным шеллу, но БЕЗ sqlite3."""
    lean = tmp / "bin-no-sqlite"
    lean.mkdir()
    for tool in ("zsh", "awk", "sed", "grep", "cat", "ls", "uname", "stty"):
        for root in ("/bin", "/usr/bin"):
            src = pathlib.Path(root) / tool
            if src.exists():
                (lean / tool).symlink_to(src)
                break
    return lean


def main() -> int:
    if not ARTIFACT.exists():
        print(f"нет артефакта: {ARTIFACT}", file=sys.stderr)
        print("собери: python -m cod_doc.cli.completion --write", file=sys.stderr)
        return 1

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = pathlib.Path(raw_tmp)
        zdotdir = str(_make_zdotdir(tmp))

        empty_home = tmp / "empty-home"
        empty_home.mkdir()
        pg_home = tmp / "pg-home"
        pg_home.mkdir()
        (pg_home / "config.yaml").write_text(
            "projects:\n"
            "- name: hubproj\n"
            "  path: /nonexistent/hubproj\n"
            "  db_url: postgresql+psycopg://user@localhost/hub\n",
            encoding="utf-8",
        )
        nowhere = tmp / "nowhere"
        nowhere.mkdir()
        lean = str(_lean_bin(tmp))

        # (метка, набираемое, cwd, env, должны ли быть кандидаты)
        cases: list[tuple[str, str, str, dict[str, str], bool]] = [
            ("команды верхнего уровня", "cod-doc ", str(REPO), {}, True),
            ("подкоманды task", "cod-doc task ", str(REPO), {}, True),
            ("слаги проектов", "cod-doc task show -p ", str(REPO), {}, True),
            (
                "task_id",
                f"cod-doc task show -p {LIVE_PROJECT} ",
                str(REPO),
                {},
                True,
            ),
            (
                "plan.scope",
                f"cod-doc plan ready -p {LIVE_PROJECT} ",
                str(REPO),
                {},
                True,
            ),
            (
                "нет реестра и нет БД вверх по дереву",
                "cod-doc task show ",
                str(nowhere),
                {"COD_DOC_HOME": str(empty_home)},
                False,
            ),
            (
                "несуществующий слаг",
                "cod-doc task show -p no-such-project ",
                str(nowhere),
                {},
                False,
            ),
            (
                "проект на Postgres",
                "cod-doc task show -p hubproj ",
                str(nowhere),
                {"COD_DOC_HOME": str(pg_home)},
                False,
            ),
            (
                "нет sqlite3 в PATH",
                f"cod-doc task show -p {LIVE_PROJECT} ",
                str(nowhere),
                {"PATH": lean},
                False,
            ),
        ]

        for label, line, cwd, env_extra, want_values in cases:
            body = run_case(zdotdir, line, cwd, env_extra)
            noisy = [ln for ln in body.splitlines() if _NOISE.search(ln)]
            # Кандидат опознаётся по разделителю `--` из _describe либо по
            # вопросу zsh «do you wish to see all N possibilities». Всё
            # остальное — эхо набранного, его в расчёт не берём.
            candidates = [ln for ln in body.splitlines() if _CANDIDATE.search(ln)]

            if noisy:
                failures.append(f"{label}: шум в промпте")
                print(f"\n✗ {label}: ШУМИТ")
                for ln in noisy[:4]:
                    print("   " + ln[:160])
                continue

            if want_values and not candidates:
                failures.append(f"{label}: ожидались кандидаты, их нет")
                print(f"\n✗ {label}: кандидатов нет")
                continue

            if not want_values and candidates:
                failures.append(f"{label}: ожидалась тишина, а кандидаты есть")
                print(f"\n✗ {label}: должно было быть тихо")
                for ln in candidates[:3]:
                    print("   " + ln[:160])
                continue

            print(f"\n✓ {label}: {'кандидаты есть' if candidates else 'тихо'}")
            for ln in candidates[:3]:
                print("   " + ln[:160])

    if failures:
        print(f"\n{len(failures)} провал(ов): " + "; ".join(failures), file=sys.stderr)
        return 1
    print(f"\nвсе {len(cases)} кейсов прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
