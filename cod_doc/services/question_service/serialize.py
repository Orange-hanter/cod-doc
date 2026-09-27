"""Question → dict — одна форма для MCP, CLI ``--json``, REST и веба."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

from .links import list_links
from .options import list_options

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import OpenQuestion, QuestionLink, QuestionOption

    from .import_doc import ImportResult


def _iso(value: datetime | None) -> str | None:
    """ISO-8601 в UTC. SQLite отдаёт время без таймзоны, объект из identity map —
    с ней; без нормализации одна и та же карточка сериализовалась бы двумя способами.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def option_to_dict(opt: QuestionOption) -> dict[str, Any]:
    return {"position": opt.position, "title": opt.title, "body": opt.body, "chosen": opt.chosen}


def link_to_dict(edge: QuestionLink) -> dict[str, Any]:
    return {
        "to_kind": edge.to_kind.value,
        "to_ref": edge.to_ref,
        "relation": edge.relation.value,
        "note": edge.note,
        "resolved": edge.resolved,
        "broken_reason": edge.broken_reason,
        "last_checked": _iso(edge.last_checked),
    }


def question_summary(q: OpenQuestion) -> dict[str, Any]:
    """Строка списка: без контекста, вариантов и ссылок."""
    return {
        "question_id": q.question_id,
        "title": q.title,
        "status": q.status.value,
        "priority": q.priority.value,
        "owner": q.owner,
        "resolved_by_adr": q.resolved_by_adr,
        "created": _iso(q.created),
        "last_updated": _iso(q.last_updated),
    }


def question_to_dict(session: Session, q: OpenQuestion) -> dict[str, Any]:
    """Полная карточка вопроса: поля + варианты + ссылки."""
    assert q.row_id is not None
    return {
        **question_summary(q),
        "question": q.question,
        "context": q.context,
        "resolution": q.resolution,
        "resolved_at": _iso(q.resolved_at),
        "source_doc_key": q.source_doc_key,
        "author": q.author,
        "options": [option_to_dict(o) for o in list_options(session, q.row_id)],
        "links": [link_to_dict(e) for e in list_links(session, q.row_id)],
    }


def import_result_to_dict(result: ImportResult) -> dict[str, Any]:
    """План импорта и что с ним сделано — одна форма для MCP и CLI ``--json``."""
    plan = result.plan
    return {
        "doc_key": plan.doc_key,
        "mode": plan.mode,
        "file_path": plan.file_path,
        "incoming_links": plan.incoming_links,
        "skipped_links": plan.skipped_links,
        "questions": [
            {
                "title": q.title,
                "status": q.status.value,
                "owner": q.owner,
                "options": [title for title, _ in q.options],
                "links": [f"{r.value} → {k.value}:{ref}" for k, ref, r in q.links],
                "context_chars": len(q.context or ""),
                "warnings": q.warnings,
            }
            for q in plan.questions
        ],
        "created": result.created,
        "document_deleted": result.document_deleted,
        "file_deleted": result.file_deleted,
    }
