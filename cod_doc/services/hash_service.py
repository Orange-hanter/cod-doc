"""ACU-002: пересчёт реестра хэшей ``MASTER.md`` со следом в журнале.

``core.hash_calc.update_hashes`` — чистая файловая операция: слой ``core``
не знает о БД и знать не должен. Но это запись в проект, и ADO-040 требует
от неё того же, что от любой мутации: activity-события с автором. Событие
живёт здесь, в слое ``services``, — поверхности зовут этот вход, а не
``core`` напрямую.

Файл пишется до события и транзакции не принадлежит: откатить запись на
диск нечем. Событие поэтому эмитится только после того, как реестр
действительно переписан, и только когда переписано хоть что-то — пустой
пересчёт не мутация.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.core.hash_calc import update_hashes
from cod_doc.services import activity_service

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

#: Вид события в ``activity_event`` — рядом с ``doc.exported``.
EVENT_KIND = "master.hashes_updated"


def update_master_hashes(
    session: Session,
    project_id: int,
    master_path: Path,
    *,
    author: str,
) -> tuple[int, list[str]]:
    """Пересчитать реестр в ``master_path`` и записать событие, если он изменился.

    Возвращает то же, что ``update_hashes``: число переписанных записей и
    предупреждения.
    """
    updated, warnings = update_hashes(master_path)
    if updated:
        activity_service.emit_for_write(
            session,
            project_id,
            EVENT_KIND,
            author,
            scope_kind="doc",
            scope_id=master_path.name,
            payload={"path": str(master_path), "updated": updated, "warnings": len(warnings)},
            summary=f"{master_path.name}: переписано записей реестра хэшей — {updated}",
        )
    return updated, warnings
