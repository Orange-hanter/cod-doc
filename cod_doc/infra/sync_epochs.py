"""Эпохи схемы: какие миграции открывают эпоху синхронизации (RFC 29 §3.6, Q-012; CLO-032).

Changeset несёт операции в форме уезжающих колонок (``SHIPPED_COLUMNS``).
Миграция, меняющая этот набор — колонку authored-таблицы, её тип, саму
authored-таблицу, — делает операции старой схемы неприменимыми на новой и
наоборот. Такая миграция **открывает эпоху**; эпоха называется ревизией,
которая её открыла. Эпоха реплики — последняя открывающая ревизия не новее
головы миграций её БД.

**Реестр** ``MIGRATIONS`` — застывший список: каждая ревизия после
``SYNC_BASELINE`` (``0049_sync_identity``, до неё синхронизации нет) записана
с решением «открывает / не открывает». Решение принимается при написании
миграции, а не выводится в рантайме: вывод из схемы стоил бы прогона
alembic, а ошибка в нём молча разделила бы реплики. Гейт
``tests/infra/test_sync_epochs_registry.py`` прогоняет цепочку по шагу на
временной SQLite и требует, чтобы решение совпало с фактом: новая миграция
без записи, «не открывает» при изменённых уезжающих колонках и «открывает»
без изменений — красные.

Модуль — infra: им пользуются ``sync_service`` (push/pull/снапшот) и
``update_service`` (фаза B, ``SYNC-106``). Исключений ``SYNC-NNN`` здесь нет —
``infra`` не импортирует ``services``; незнакомая ревизия — ``LookupError``,
сервисы переводят её в свой код.
"""

from __future__ import annotations

import functools
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "MIGRATIONS",
    "SYNC_BASELINE",
    "UnknownRevisionError",
    "code_head",
    "epoch_is_newer",
    "epoch_of",
    "epochs",
    "epochs_opened_between",
    "revision_order",
]

#: Последняя ревизия до эпох: ``uid`` и служебные таблицы (0049). Ревизии не
#: новее неё в реестр не входят — на них синхронизации нет.
SYNC_BASELINE: Final = "0049_sync_identity"

#: Ревизия → открывает ли она эпоху. Порядок — порядок цепочки миграций.
#: Новая миграция дописывается сюда в том же PR; гейт сверяет решение с
#: фактическим изменением уезжающих колонок.
MIGRATIONS: Mapping[str, bool] = MappingProxyType(
    {
        # rank в десяти местах порядка и document.node_rank — уезжающие колонки (Q-009).
        "0050_sync_rank": True,
        # sync_conflict — local-таблица, authored не трогает.
        "0051_sync_conflict": False,
        # sync_conflict.opened_hlc — колонка local-таблицы.
        "0052_sync_conflict_opened_hlc": False,
        # actor, api_token, project_member — local-таблицы хранилища доступа (CLO-034).
        "0053_sync_access": False,
        # plan.rank — уезжающая колонка: порядок планов проекта (RFC 36, ROAD-001).
        "0054_plan_rank": True,
    }
)


class UnknownRevisionError(LookupError):
    """Ревизия незнакома поставляемой цепочке миграций."""


@functools.cache
def revision_order() -> Mapping[str, int]:
    """Ревизии поставляемых миграций → порядковый номер от корня.

    Каталог миграций берётся через ``as_file``: у ``files()`` из wheel или zip
    ``str()`` — не путь на ФС, и ``ScriptDirectory`` молча увидел бы пустую
    цепочку. Пустая цепочка — отказ, а не «всё новее».
    """
    from importlib.resources import as_file, files

    from alembic.script import ScriptDirectory

    with as_file(files("cod_doc.infra.migrations")) as path:
        chain = [rev.revision for rev in ScriptDirectory(str(path)).walk_revisions()]
    if not chain:
        msg = "цепочка миграций не прочитана — порядок схем неизвестен"
        raise UnknownRevisionError(msg)
    return MappingProxyType({rev: i for i, rev in enumerate(reversed(chain))})


def code_head() -> str:
    """Голова поставляемой цепочки миграций."""
    order = revision_order()
    return max(order, key=lambda rev: order[rev])


def epochs() -> tuple[str, ...]:
    """Открывающие ревизии в порядке цепочки."""
    return tuple(rev for rev, opens in MIGRATIONS.items() if opens)


def _position(revision: str) -> int:
    order = revision_order()
    if revision not in order:
        msg = f"ревизия {revision} незнакома коду"
        raise UnknownRevisionError(msg)
    return order[revision]


def epoch_of(schema_head: str) -> str | None:
    """Эпоха схемы ``schema_head``: последняя открывающая ревизия не новее неё.

    ``None`` — схема старше первой эпохи (синхронизации на ней нет).
    Незнакомая ревизия — ``UnknownRevisionError``.
    """
    here = _position(schema_head)
    current: str | None = None
    for rev in epochs():
        if _position(rev) > here:
            break
        current = rev
    return current


def epoch_is_newer(candidate: str, than: str) -> bool:
    """``candidate`` — эпоха новее ``than``.

    Незнакомая коду ``candidate`` — заведомо новее: её открыл релиз, которого
    у реплики ещё нет (как ``schema_is_newer`` у changeset'а). Незнакомая
    ``than`` — ``UnknownRevisionError``: это своя эпоха, сравнить не с чем.
    """
    base = _position(than)
    order = revision_order()
    if candidate not in order:
        return True
    return order[candidate] > base


def epochs_opened_between(current: str | None, target: str) -> list[str]:
    """Эпохи, которые откроет ``alembic upgrade`` с ``current`` до ``target``.

    ``current`` — ``None`` (пустая БД) или незнакомая коду ревизия (БД новее
    кода; alembic откажет сам) — эпох нет: операций старой эпохи в такой БД
    быть не может или проверять их не этому коду.
    """
    order = revision_order()
    if current is None or current not in order:
        return []
    low, high = order[current], _position(target)
    return [rev for rev in epochs() if low < _position(rev) <= high]
