"""`zsh -n` над артефактом и над prelude.

Сломанный синтаксис в completion не падает с ошибкой — он просто молча
перестаёт дополнять, и разбираться приходится в шелле. Дешевле поймать здесь.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from cod_doc.cli.completion import ARTIFACT_PATH, PRELUDE_PATH

_TIMEOUT = 30

pytestmark = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh не установлен")


def _parse(path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["zsh", "-n", path],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )


def test_artifact_parses_as_zsh() -> None:
    proc = _parse(str(ARTIFACT_PATH))
    assert proc.returncode == 0, proc.stderr


def test_prelude_parses_standalone() -> None:
    """Prelude — настоящий .zsh, а не строковая константа: его можно проверить."""
    proc = _parse(str(PRELUDE_PATH))
    assert proc.returncode == 0, proc.stderr


#: Специальные параметры zsh. Объявить такой `local` — значит тихо сломать
#: поведение шелла: `local path` рвёт $PATH (функция перестаёт находить свою
#: запись в реестре и молчит), `local status` ломает проверки кода возврата,
#: `local reply` конфликтует с соглашением compsys. Список неполный по
#: замыслу — здесь только то, что реально может понадобиться как рабочее имя.
_ZSH_SPECIALS = frozenset(
    {
        "argv",
        "cdpath",
        "commands",
        "fignore",
        "fpath",
        "functions",
        "histchars",
        "mailpath",
        "manpath",
        "options",
        "parameters",
        "path",
        "prompt",
        "psvar",
        "random",
        "reply",
        "signals",
        "status",
    }
)


def test_prelude_avoids_zsh_special_parameter_names() -> None:
    """`local path` ломает $PATH — ловили на живом коде, ошибка была тихой."""
    offenders: list[str] = []
    for lineno, line in enumerate(PRELUDE_PATH.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("local "):
            continue
        declared = stripped.removeprefix("local ").split()
        for name in declared:
            bare = name.lstrip("-").split("=", 1)[0]
            if bare in _ZSH_SPECIALS:
                offenders.append(f"prelude.zsh:{lineno}: local {bare}")
    assert not offenders, "специальные параметры zsh нельзя брать локальными: " + "; ".join(
        offenders
    )
