"""Починка после ``cod-doc update`` ждёт перескока реплики на снапшот эпохи (CLO-053).

Фаза D ``update`` (``repair_service.apply``: ``doc import`` дрейфа, раскладка
дерева, ссылки, снятие замков) пишет authored-данные, то есть копит хвост
журнала. Если ``update`` поднял схему на новую эпоху, а голова реплики осталась
в прошлой, следующий ``pull`` перескакивает на снапшот эпохи (CLO-050) — и
отказывает ``SYNC-105`` любой реплике с хвостом (``epoch_rebase.refuse_pending``).
Своей же починкой ``update`` гарантировал бы этот отказ.

Поэтому:

1. **Фаза D** (``defer_if_needed``) для такой реплики — синхронизируемый проект
   (есть remote) и голова в эпохе старше эпохи схемы — не чинит, а ставит
   отметку в local-колонке ``project.config_json`` (не уезжает, хвоста не
   создаёт) и отчитывается «отложено до pull».
2. **Pull** (``run_if_deferred``) после перескока — или обычного pull, если
   remote эпоху ещё не открывал и перескок не нужен — снимает отметку и чинит
   в своей транзакции. Записи починки захватываются как обычно и уезжают
   следующим push.

Отметка живёт до успешной починки: упал pull — отметка осталась, починка
выполнится следующим pull.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from cod_doc.infra.models import ChangesetModel, ProjectModel
from cod_doc.services.sync_service import _local, epochs

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import ProjectEntry
    from cod_doc.services.repair_service import RepairResult

__all__ = ["FLAG", "defer_if_needed", "is_deferred", "run_if_deferred"]

#: Ключ отметки в ``project.config_json`` (local-колонка, RFC 29 §3.2).
FLAG: Final = "repair_deferred_until_pull"


def _head_epoch_behind(session: Session, project_id: int) -> str | None:
    """Эпоха головы реплики, если она старше эпохи схемы этой БД; иначе ``None``."""
    head = _local.local_head(session, project_id)
    row = session.get(ChangesetModel, head) if head else None
    if row is None:
        return None
    current = _local.epoch(session)
    return row.epoch if epochs._newer(current, row.epoch) else None


def defer_if_needed(session: Session, project_id: int, *, has_remote: bool) -> str | None:
    """Отложить починку, если перескок впереди; вернуть эпоху головы или ``None``.

    Без remote перескока не будет — чинить сразу. Отметка пишется в
    local-колонку: хвоста журнала она не создаёт.
    """
    if not has_remote:
        return None
    behind = _head_epoch_behind(session, project_id)
    if behind is None:
        return None
    project = session.get_one(ProjectModel, project_id)
    project.config_json = {**(project.config_json or {}), FLAG: True}
    return behind


def is_deferred(session: Session, project_id: int) -> bool:
    project = session.get(ProjectModel, project_id)
    return bool(project is not None and (project.config_json or {}).get(FLAG))


def run_if_deferred(sf: sessionmaker[Session], project: str, *, author: str) -> RepairResult | None:
    """После pull: отложенная починка — выполнить и снять отметку; не было — ``None``.

    Проекта нет в реестре машины (``root``/``MASTER.md`` неоткуда взять) —
    отметка остаётся до ``update``, который знает пути.
    """
    from cod_doc.config import Config
    from cod_doc.infra.db import transactional
    from cod_doc.services import repair_service

    with transactional(sf, commit=False) as session:
        # Мягкий поиск: проекта под этим слагом в БД может не быть (clone ещё
        # сверяет слаг сам) — тогда и откладывать было нечего.
        project_id = session.scalar(select(ProjectModel.row_id).where(ProjectModel.slug == project))
        if project_id is None or not is_deferred(session, project_id):
            return None
    entry: ProjectEntry | None = Config.load().get_project(project)
    if entry is None:
        return None
    with transactional(sf) as session:
        result = repair_service.apply(
            session,
            project_id=project_id,
            root_path=entry.root,
            master_path=entry.master_path,
            slug=project,
            author=author,
        )
        project_row = session.get_one(ProjectModel, project_id)
        config = dict(project_row.config_json or {})
        config.pop(FLAG, None)
        project_row.config_json = config
    return result
