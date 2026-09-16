"""Генерация zsh-completion из живого click-дерева.

Пакет намеренно **не импортируется** из ``cod_doc/cli/__init__.py`` — кроме
трёхстрочного ``cmd.py``. Старт CLI и без того стоит ~470 мс (26 групп
импортируются жадно, ``cmd_hub`` тянет alembic + SQLAlchemy), и генератор не
имеет права эту цифру увеличивать. Инвариант стережёт
``tests/cli/test_zsh_completion_drift.py``.

Артефакт ``_cod-doc`` лежит рядом и коммитится в репозиторий: ставится он
симлинком в ``$fpath``, а ``cod-doc completion zsh`` печатает его дословно.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: Каталог пакета — он же каталог package-data (`_cod-doc`, `prelude.zsh`).
PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent

#: Сгенерированный и закоммиченный zsh-completion.
ARTIFACT_PATH: Final[Path] = PACKAGE_DIR / "_cod-doc"

#: Рукописный runtime — вшивается в артефакт дословно.
PRELUDE_PATH: Final[Path] = PACKAGE_DIR / "prelude.zsh"

#: Команда регенерации; печатается в сообщении упавшего anti-drift теста.
REGEN_COMMAND: Final[str] = "python -m cod_doc.cli.completion --write"

__all__ = ["ARTIFACT_PATH", "PACKAGE_DIR", "PRELUDE_PATH", "REGEN_COMMAND"]
