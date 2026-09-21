"""ADO-192: исполнитель автопочинки состояния проекта (фаза D команды ``update``).

Диагноз целиком делегирован :func:`cod_doc.services.curator_service.next` —
второй диагност завести нельзя, он обязан разъехаться с первым. Новое здесь
только исполнение: куратор кладёт на каждый пункт очереди готовую команду в
``suggested_action``, но сам ничего не делает.

Чинятся ровно четыре класса находок:

===================  =========================================================
``edited_in_place``  файл правлен на диске, БД отстала → ``import_document``
hash ``STALE``       реестр ``MASTER.md`` разошёлся с файлом → ``update_hashes``
битые ссылки         derived-таблица ``link`` протухла → sync + resolve секций
протухшие замки      ``checked_out_at`` старше TTL → ``release_stale``
===================  =========================================================

Не чинятся никогда: ``stale_export`` (в files-are-source режиме это норма),
``missing`` (вслепую не пересоздаём), hash ``BROKEN`` (пересчёт только
предупредит, файла всё равно нет), ``unplaced`` и внешние findings. Все они
уходят в ``RepairResult.reported_only`` счётчиками. ``doc export`` не зовётся
вовсе — он под guard'ом до byte-identical round-trip.

Порядок исполнения задан протоколом ``plugins/cod-doc/commands/drift.md`` и
менять его нельзя: если импортированный файл входит в реестр хэшей корневого
``MASTER.md``, после импорта идёт ``update_hashes``, и только затем
``doc import MASTER.md`` — иначе реестр разъедется с файлами.

.. warning::

   ``dry_run`` **не** покрывается одним ``commit=False``.
   ``hash_calc.update_hashes`` пишет ``MASTER.md`` на диск безусловно и
   dry-run не имеет. Поэтому :func:`apply` принимает ``dry_run`` явно и при
   нём не зовёт ``update_hashes`` вовсе: план строится из
   ``check_stale_refs``, который уже возвращает ``path/expected/actual`` —
   это и есть diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

__all__ = [
    "DEFAULT_TTL_MINUTES",
    "RepairAction",
    "RepairPlan",
    "RepairResult",
    "apply",
    "backfill_project_links",
    "diagnose",
]

#: TTL замка по умолчанию — тот же, что у ``checkout_service.release_stale``.
DEFAULT_TTL_MINUTES = 30

#: Виды действий. Строки попадают в отчёт и в JSON, поэтому это контракт.
KIND_DOC_IMPORT = "doc_import"
KIND_HASH_UPDATE = "hash_update"
KIND_LINK_BACKFILL = "link_backfill"
KIND_RELEASE_STALE = "release_stale"


@dataclass(slots=True)
class RepairAction:
    """Одно действие починки: что сделали (или сделали бы) и с чем."""

    kind: str
    ref: str
    applied: bool = False
    detail: str | None = None
    error: str | None = None


@dataclass(slots=True)
class RepairPlan:
    """Что чинится и что только докладывается — до всякой записи."""

    project: str
    curable: dict[str, int] = field(default_factory=dict)
    reported_only: dict[str, int] = field(default_factory=dict)
    actions: list[RepairAction] = field(default_factory=list)


@dataclass(slots=True)
class RepairResult:
    """Итог починки одного проекта."""

    project: str
    dry_run: bool
    actions: list[RepairAction] = field(default_factory=list)
    reported_only: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Ни одной ошибки — ни на уровне проекта, ни в отдельном действии."""
        raise NotImplementedError

    @property
    def applied(self) -> int:
        """Сколько действий реально применено."""
        raise NotImplementedError

    def as_dict(self) -> dict[str, Any]:
        """Плоский JSON-safe снимок: ни Path, ни datetime наружу не уходят."""
        raise NotImplementedError


def diagnose(
    session: Session,
    *,
    project_id: int,
    root_path: Path,
    master_path: Path,
    slug: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> RepairPlan:
    """Собрать план починки. Read-only: звать под ``transactional(sf, commit=False)``.

    Источник находок — ``curator_service.next``; отбор — whitelist из
    докстринга модуля.
    """
    raise NotImplementedError


def apply(
    session: Session,
    *,
    project_id: int,
    root_path: Path,
    master_path: Path,
    slug: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    dry_run: bool = False,
    author: str = "cli:update",
) -> RepairResult:
    """Применить починку. Одна упавшая чинилка не обрывает остальные.

    Семантика та же, что у ``project_service.migrate_registered_projects``:
    ошибка попадает в отчёт, а не наверх.

    ADO-040: при ``not dry_run`` эмитит одно сводное событие
    ``project.repaired`` в той же транзакции. Точечные ``doc.imported`` /
    ``task.released`` эмитят вызываемые сервисы сами.
    """
    raise NotImplementedError


def backfill_project_links(session: Session, project_id: int) -> tuple[int, int, int]:
    """Пересобрать derived-таблицу ``link`` по всем секциям проекта.

    Возвращает ``(секций, ссылок, документов)``. Логика переехала сюда из
    ``cli/link.py::link_backfill`` (COD-079), которая ходила в БД прямым
    ``select(SectionModel…)`` из presentation — против правила «прямых
    SQL-запросов из presentation нет». CLI теперь зовёт эту функцию.
    """
    raise NotImplementedError
