"""Anti-drift: каждый хук из `hooks/` перечислен в установщике.

Файл в каталоге, которого нет в списке `install.sh`, не ставится никому и
молча не работает — ровно то, что случилось бы с `pre-push`, добавь его
кто-то без правки установщика.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO_ROOT / "hooks"
INSTALLER = HOOKS_DIR / "install.sh"


def _hook_files() -> set[str]:
    """Файлы-хуки: всё в `hooks/`, кроме самого установщика."""
    return {p.name for p in HOOKS_DIR.iterdir() if p.is_file() and p.name != "install.sh"}


def _listed_hooks() -> list[set[str]]:
    """Наборы имён из каждого `for hook in … ; do` установщика."""
    text = INSTALLER.read_text(encoding="utf-8")
    return [set(m.split()) for m in re.findall(r"for hook in ([^;]+); do", text)]


def test_installer_lists_every_hook_file() -> None:
    loops = _listed_hooks()
    assert loops, "в install.sh не найден цикл `for hook in …`"
    on_disk = _hook_files()
    for listed in loops:
        assert listed == on_disk, (
            f"install.sh ставит {sorted(listed)}, а в hooks/ лежит {sorted(on_disk)}"
        )


def test_installer_and_uninstaller_agree() -> None:
    """install и remove обязаны ходить по одному и тому же списку."""
    loops = _listed_hooks()
    assert len(loops) == 2, f"ожидались циклы install и remove, найдено {len(loops)}"
    assert loops[0] == loops[1]


def test_hook_files_are_executable() -> None:
    """Неисполняемый файл git молча игнорирует — хук просто не сработает."""
    not_executable = sorted(
        p.name for p in HOOKS_DIR.iterdir() if p.is_file() and not p.stat().st_mode & 0o111
    )
    assert not not_executable, f"без +x: {not_executable}"
