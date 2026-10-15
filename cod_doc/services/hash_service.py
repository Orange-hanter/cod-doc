"""ACU-002 / DEBT-009: реестр хэшей ``MASTER.md`` — чтение и пересчёт со следом.

``core.hash_calc`` считает хэши по чистому тексту: слой ``core`` не знает о
БД и знать не должен. Здесь решается, **где** лежит реестр.

DEBT-009: если ``MASTER.md`` — документ проекта, реестр живёт в БД и
пересчёт пишет туда — секции через ``doc_service.patch_section``, преамбулу
через ``doc_service.update_metadata`` — со своими ревизиями, внутри
транзакции вызывающего. Файл не трогается: он проекция, а проекция законно
отстаёт (``stale_export``). Раньше пересчёт переписывал файл, и за ним
обязан был идти ``doc import MASTER.md`` — последний шаг «сначала файл,
потом БД» в протоколе. По той же причине и находки ``STALE`` читаются из
БД: иначе отстающий файл держал бы их открытыми вечно.

``MASTER.md``, который документом не заведён, пересчитывается в файле, как
раньше: файл тогда и есть единственный реестр.

ADO-040 / ACU-002: пересчёт — мутация проекта, поэтому событие
``master.hashes_updated`` с автором; пустой пересчёт не мутация и события
не даёт.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.core.hash_calc import recompute_hashes, stale_refs_in, update_hashes
from cod_doc.services import activity_service, doc_service, search_service

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Document

#: Вид события в ``activity_event`` — рядом с ``doc.exported``.
EVENT_KIND = "master.hashes_updated"


def _master_document(
    session: Session, project_id: int, master_path: Path, root_path: Path
) -> Document | None:
    """Документ проекта, чья проекция — ``master_path``; None, если не заведён."""
    if not master_path.is_relative_to(root_path):
        return None
    rel = master_path.relative_to(root_path).as_posix()
    return doc_service.get_by_path(session, project_id, rel)


def registry_in_db(session: Session, project_id: int, master_path: Path, root_path: Path) -> bool:
    """Заведён ли ``MASTER.md`` документом проекта — то есть пишет ли пересчёт в БД."""
    return _master_document(session, project_id, master_path, root_path) is not None


def registry_text(session: Session, project_id: int, master_path: Path, root_path: Path) -> str:
    """Текст реестра: тело документа из БД, иначе файл; нет ни того ни другого — ``""``."""
    doc = _master_document(session, project_id, master_path, root_path)
    if doc is not None and doc.row_id is not None:
        return doc_service.render_body(session, doc.row_id, project_id=project_id) or ""
    return master_path.read_text(encoding="utf-8") if master_path.exists() else ""


def stale_refs(
    session: Session, project_id: int, master_path: Path, root_path: Path
) -> list[dict[str, str]]:
    """Находки реестра ``BROKEN`` / ``STALE`` — по тому реестру, что авторитетен."""
    return stale_refs_in(registry_text(session, project_id, master_path, root_path), root_path)


def update_master_hashes(
    session: Session,
    project_id: int,
    master_path: Path,
    *,
    author: str,
    root_path: Path | None = None,
) -> tuple[int, list[str]]:
    """Пересчитать реестр и записать событие, если он изменился.

    ``root_path`` — корень проекта, от него считаются пути ссылок и путь
    документа; по умолчанию — каталог ``master_path``. Возвращает число
    переписанных записей и предупреждения.
    """
    root = root_path if root_path is not None else master_path.parent
    doc = _master_document(session, project_id, master_path, root)
    if doc is None or doc.row_id is None:
        updated, warnings = update_hashes(master_path)
        target = "file"
    else:
        updated, warnings = _update_in_db(session, doc, root, author=author)
        target = "db"
    if updated:
        activity_service.emit_for_write(
            session,
            project_id,
            EVENT_KIND,
            author,
            scope_kind="doc",
            scope_id=master_path.name,
            payload={
                "path": str(master_path),
                "updated": updated,
                "warnings": len(warnings),
                "target": target,
            },
            summary=f"{master_path.name}: переписано записей реестра хэшей — {updated}",
        )
    return updated, warnings


def _update_in_db(
    session: Session, doc: Document, root: Path, *, author: str
) -> tuple[int, list[str]]:
    """Пересчёт реестра в преамбуле и секциях документа; пишутся только изменённые части."""
    assert doc.row_id is not None
    sections = sorted(
        doc_service.get_sections(session, doc.row_id, project_id=doc.project_id),
        key=lambda s: s.position,
    )
    texts = [doc.preamble, *(s.body for s in sections)]
    new_texts, updated, warnings = recompute_hashes(texts, root)
    if not updated:
        return 0, warnings

    reason = "hash update (DEBT-009)"
    # Атомарно: реестр — одно целое (строка таблицы и блок её документа могут
    # стоять в разных секциях). Падение на k-й секции откатывает и преамбулу,
    # и секции 0…k-1, а не оставляет реестр наполовину переписанным в
    # транзакции вызывающего (repair коммитит остальные действия).
    with session.begin_nested():
        if new_texts[0] != doc.preamble:
            doc_service.update_metadata(
                session,
                project_id=doc.project_id,
                document_id=doc.row_id,
                author=author,
                preamble=new_texts[0],
                reason=reason,
                reindex=False,
            )
        for section, new_body in zip(sections, new_texts[1:], strict=True):
            if new_body != section.body:
                doc_service.patch_section(
                    session,
                    project_id=doc.project_id,
                    document_id=doc.row_id,
                    anchor=section.anchor,
                    new_body=new_body,
                    author=author,
                    reason=reason,
                    reindex=False,
                )
        # Один пересчёт индекса на документ, а не по разу на секцию.
        search_service.index_doc(session, project_id=doc.project_id, doc_key=doc.doc_key)
    return updated, warnings
