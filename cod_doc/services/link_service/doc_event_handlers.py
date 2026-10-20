"""Подписчики `link_service` на события документов (SVC-007).

Ровно то, что раньше `doc_service` звал напрямую, только теперь
направление зависимости одно: link_service знает про doc_service и
doc_events, doc_service про link_service — нет. Подписывает их
`services/event_wiring.install()`, а не импорт этого модуля: побочный эффект
импорта зависел бы от того, кто и когда успел импортировать пакет.
"""

from __future__ import annotations

from cod_doc.services import doc_events
from cod_doc.services.doc_events import (
    DocumentRenamed,
    Rewrites,
    SectionAnchorChanged,
    SectionWritten,
)

from .anchor_cascade import anchor_cascade
from .rename_cascade import rename_cascade
from .resolver import resolve_section, sync_section


def on_section_written(event: SectionWritten) -> None:
    """COD-079: перечитать ссылки секции после записи тела.

    Сначала ``sync`` (удалить и разобрать заново из тела), чтобы правка тела
    сняла устаревшие строки; потом ``resolve`` заполняет ``to_doc_key`` /
    ``to_task_id`` там, где цель существует. Один ``resolve`` не увидел бы
    правку тела, если строки уже были.
    """
    sync_section(event.session, event.section_id)
    resolve_section(event.session, event.section_id)


def on_section_anchor_changed(event: SectionAnchorChanged) -> Rewrites:
    report = anchor_cascade(
        event.session,
        project_id=event.project_id,
        document_id=event.document_id,
        old_anchor=event.old_anchor,
        new_anchor=event.new_anchor,
        author=event.author,
        reason=event.reason,
    )
    return Rewrites(report.rewritten, report.links)


def on_document_renamed(event: DocumentRenamed) -> None:
    rename_cascade(
        event.session,
        project_id=event.project_id,
        old_doc_key=event.old_doc_key,
        new_doc_key=event.new_doc_key,
        author=event.author,
        reason=event.reason,
        path_map=event.path_map,
    )


def register() -> None:
    doc_events.subscribe(SectionWritten, on_section_written)
    doc_events.subscribe(SectionAnchorChanged, on_section_anchor_changed)
    doc_events.subscribe(DocumentRenamed, on_document_renamed)
