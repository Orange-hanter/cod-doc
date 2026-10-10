"""RFC 27 F13 — локальность задачи по её ``affects_files``.

Задача, у которой ВСЕ затронутые файлы лежат в чужом репозитории (абсолютные
пути вне ``root_path`` проекта), бесполезна coding-агенту, работающему в этом
репо: взять её в работу здесь нечем. Живые данные (ADO-071…074, план
`adoption-2026-08`) решено 2026-09-23 не переносить — достаточно фильтра
``local_only`` в ready-выборках.

Только stdlib (``os.path``, ``pathlib.PurePath``), обращений к ФС нет.
"""

from __future__ import annotations

import os
from pathlib import PurePath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _inside(path: str, root: PurePath) -> bool:
    return PurePath(os.path.normpath(path)).is_relative_to(root)


def is_foreign(paths: Sequence[str], root_path: str) -> bool:
    """True, если задача «чужая»: ``paths`` непуст и КАЖДЫЙ путь абсолютный
    и лежит вне ``root_path``.

    Пустой список, относительный путь или ``~/…`` (не абсолютный)
    делают задачу локальной. Пустой ``root_path`` — всегда False.
    Принадлежность корню — через ``PurePath.is_relative_to``, а не
    ``startswith``: ``/a/bc/x`` не лежит внутри ``/a/b``.
    """
    if not paths or not root_path:
        return False
    root = PurePath(os.path.normpath(root_path))
    return all(PurePath(p).is_absolute() and not _inside(p, root) for p in paths)


def foreign_paths(paths: Sequence[str], root_path: str) -> list[str]:
    """Абсолютные пути вне корня в исходном порядке.

    Используется предупреждением ``task_create`` / ``task_update`` при путях
    вне ``root_path`` проекта.
    """
    if not root_path:
        return []
    root = PurePath(os.path.normpath(root_path))
    return [p for p in paths if PurePath(p).is_absolute() and not _inside(p, root)]


def foreign_paths_warning(paths: Sequence[str], root_path: str) -> str | None:
    """AFT-012: текст предупреждения о путях вне корня или ``None``, если таких нет.

    Общий для ``task_create`` (MCP) и ``task_service.update_affects_files``
    (AFT-021) — запись не отклоняется, вызывающий получает только текст.
    Сам ``root_path`` в текст не попадает: это раскладка ФС машины, которую
    клиент не передавал; перечисляются только пути из его же запроса. Без
    ``root_path`` у проекта предупреждает об абсолютных путях отдельно.
    """
    if not root_path:
        # Без корня локальность абсолютных путей не проверить — молчать об
        # этом значило бы выдать их за свои.
        absolute = [p for p in paths if PurePath(p).is_absolute()]
        if not absolute:
            return None
        return f"root_path проекта не задан — локальность не проверить для: {', '.join(absolute)}"
    foreign = foreign_paths(paths, root_path)
    if not foreign:
        return None
    return (
        "affects_files вне корня проекта: "
        f"{', '.join(foreign)}; task_next_ready/plan_ready "
        "по умолчанию (local_only=true) такую задачу не выдадут, "
        "если все её файлы чужие"
    )
