"""Лимиты bulk-импорта, отделённые от тяжёлого кода импортёра.

Константа живёт здесь, а не в ``services/restate_importer.py``, потому что
её читает декоратор опции ``--max-files`` в ``cli/cmd_import.py``, то есть на
уровне модуля. Импорт ради одного int'а тянул бы за собой весь импортёр, а с
ним SQLAlchemy — и это оплачивал бы каждый вызов CLI, включая ``--help``
(ADO-179).

``restate_importer`` ре-экспортирует имя, так что для остального кода ничего
не меняется.
"""

from __future__ import annotations

from typing import Final

#: Потолок числа файлов в одном прогоне импорта.
DEFAULT_MAX_FILES: Final = 1000

__all__ = ["DEFAULT_MAX_FILES"]
