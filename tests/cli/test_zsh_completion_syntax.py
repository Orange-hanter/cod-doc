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


def test_prelude_declares_no_local_named_path() -> None:
    """`path` в zsh привязан к $PATH — локальная переменная ломает поиск команд.

    Ошибка тихая: функция просто перестаёт находить свою запись в реестре.
    Ловили на живом коде, поэтому проверяем явно.
    """
    for lineno, line in enumerate(PRELUDE_PATH.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("local "):
            continue
        names = stripped.removeprefix("local ").split()
        assert "path" not in names, f"prelude.zsh:{lineno}: `local path` ломает $PATH"
