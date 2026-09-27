"""Question edges — what a question is about, blocks, or is answered by.

``to_ref`` is validated for shape on write and for existence by
:func:`cod_doc.services.question_service.verify.verify_links`: a question is
often written before the task that will answer it exists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from cod_doc.domain.entities import (
    OpenQuestion,
    QuestionLink,
    QuestionLinkKind,
    QuestionRelation,
    QuestionStatus,
)
from cod_doc.infra.models import OpenQuestionModel, QuestionLinkModel
from cod_doc.infra.repositories import OpenQuestionRepository, QuestionLinkRepository
from cod_doc.services import activity_service, search_service

from ._internals import _require_question, audit_kwargs, validate_ref

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _record(
    session: Session,
    model: OpenQuestionModel,
    *,
    author: str,
    op: str,
    reason: str | None,
    summary: str,
    payload: dict[str, object],
) -> None:
    """Revision + activity event + FTS refresh — один вызов на мутацию (ADO-040)."""
    session.flush()
    activity_service.write_revision_and_emit_event(
        session,
        **audit_kwargs(
            model, author=author, op=op, reason=reason, summary=summary, payload=payload
        ),
    )
    search_service.index_question(session, model)
    session.flush()


def list_links(session: Session, question_row_id: int) -> list[QuestionLink]:
    return QuestionLinkRepository(session).list_for_question(question_row_id)


def _find_edge(
    model: OpenQuestionModel,
    to_kind: QuestionLinkKind,
    to_ref: str,
    relation: QuestionRelation,
) -> QuestionLinkModel | None:
    for edge in model.links:
        if (
            edge.to_kind == to_kind.value
            and edge.to_ref == to_ref
            and edge.relation == relation.value
        ):
            return edge
    return None


def link(
    session: Session,
    *,
    project_id: int,
    question_id: str,
    to_kind: QuestionLinkKind,
    to_ref: str,
    relation: QuestionRelation,
    author: str,
    note: str | None = None,
    reason: str | None = None,
) -> QuestionLink:
    """Attach an edge. Re-attaching the same edge only updates its note."""
    validate_ref(to_kind, to_ref)
    model = _require_question(session, project_id, question_id)
    repo = QuestionLinkRepository(session)

    existing = _find_edge(model, to_kind, to_ref, relation)
    if existing is not None:
        if note is not None and note != existing.note:
            existing.note = note or None
            session.flush()
        return repo._to_domain(existing)

    edge = QuestionLinkModel(
        to_kind=to_kind.value, to_ref=to_ref, relation=relation.value, note=note
    )
    model.links.append(edge)
    model.last_updated = datetime.now(UTC)
    payload: dict[str, object] = {
        "to_kind": to_kind.value,
        "to_ref": to_ref,
        "relation": relation.value,
    }
    _record(
        session,
        model,
        author=author,
        op="linked",
        reason=reason,
        summary=f"Question {question_id} {relation.value} → {to_kind.value}:{to_ref}",
        payload=payload,
    )
    return repo._to_domain(edge)


def unlink(
    session: Session,
    *,
    project_id: int,
    question_id: str,
    to_kind: QuestionLinkKind,
    to_ref: str,
    relation: QuestionRelation,
    author: str,
    reason: str | None = None,
) -> bool:
    """Detach an edge. Returns False when there was nothing to detach."""
    model = _require_question(session, project_id, question_id)
    existing = _find_edge(model, to_kind, to_ref, relation)
    if existing is None:
        return False
    model.links.remove(existing)
    model.last_updated = datetime.now(UTC)
    _record(
        session,
        model,
        author=author,
        op="unlinked",
        reason=reason,
        summary=f"Question {question_id} ⊘ {to_kind.value}:{to_ref}",
        payload={"to_kind": to_kind.value, "to_ref": to_ref, "relation": relation.value},
    )
    return True


def questions_for_targets(
    session: Session,
    project_id: int,
    targets: list[tuple[QuestionLinkKind, str]],
    *,
    status: QuestionStatus | None = QuestionStatus.OPEN,
) -> dict[tuple[str, str], list[OpenQuestion]]:
    """Questions linked to each target, keyed by ``(to_kind, to_ref)``.

    One query for the whole batch — used by task/context views that show
    «вопросы по этой задаче» next to many targets at once.
    """
    if not targets:
        return {}
    kinds = {k.value for k, _ in targets}
    refs = {r for _, r in targets}
    stmt = (
        select(QuestionLinkModel.to_kind, QuestionLinkModel.to_ref, OpenQuestionModel)
        .join(OpenQuestionModel, OpenQuestionModel.row_id == QuestionLinkModel.question_row_id)
        .where(
            OpenQuestionModel.project_id == project_id,
            QuestionLinkModel.to_kind.in_(kinds),
            QuestionLinkModel.to_ref.in_(refs),
        )
        .order_by(OpenQuestionModel.question_id)
    )
    if status is not None:
        stmt = stmt.where(OpenQuestionModel.status == status.value)
    wanted = {(k.value, r) for k, r in targets}
    repo = OpenQuestionRepository(session)
    out: dict[tuple[str, str], list[OpenQuestion]] = {}
    for kind, ref, model in session.execute(stmt).all():
        key = (kind, ref)
        if key not in wanted:
            continue
        bucket = out.setdefault(key, [])
        if all(q.question_id != model.question_id for q in bucket):
            bucket.append(repo._to_domain(model))
    return out


def linked_questions(
    session: Session,
    project_id: int,
    *,
    target_kind: QuestionLinkKind,
    target_ref: str,
    status: QuestionStatus | None = None,
) -> list[dict[str, object]]:
    """Вопросы, ссылающиеся на цель; для документа — и на любую его секцию.

    Обратная навигация «задача/документ → вопросы». Запись —
    ``question_id``, ``title``, ``status``, ``priority`` и ``relation``;
    открытые первыми, вопрос с несколькими рёбрами к цели — один раз.
    """
    conds = [
        (QuestionLinkModel.to_kind == target_kind.value) & (QuestionLinkModel.to_ref == target_ref)
    ]
    if target_kind is QuestionLinkKind.DOCUMENT:
        conds.append(
            (QuestionLinkModel.to_kind == QuestionLinkKind.SECTION.value)
            & QuestionLinkModel.to_ref.startswith(f"{target_ref}#", autoescape=True)
        )
    stmt = (
        select(OpenQuestionModel, QuestionLinkModel.relation)
        .join(QuestionLinkModel, QuestionLinkModel.question_row_id == OpenQuestionModel.row_id)
        .where(OpenQuestionModel.project_id == project_id, or_(*conds))
        .order_by(OpenQuestionModel.question_id)
    )
    if status is not None:
        stmt = stmt.where(OpenQuestionModel.status == status.value)
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for model, relation in session.execute(stmt).all():
        if model.question_id not in seen:
            seen.add(model.question_id)
            out.append(_hint_entry(model, relation))
    out.sort(key=lambda e: e["status"] != QuestionStatus.OPEN.value)
    return out


def open_questions_for_context(
    session: Session,
    project_id: int,
    *,
    target_kind: QuestionLinkKind | None,
    target_ref: str | None,
    limit: int,
) -> list[dict[str, object]]:
    """Открытые вопросы для ``context_get.hints.open_questions``.

    Сначала — связанные с целью (:func:`linked_questions`), затем добор
    срочными (critical/high) по проекту с ``relation=None``.
    """
    picked: list[dict[str, object]] = []
    if target_kind is not None and target_ref is not None:
        picked = linked_questions(
            session,
            project_id,
            target_kind=target_kind,
            target_ref=target_ref,
            status=QuestionStatus.OPEN,
        )
    seen = {str(e["question_id"]) for e in picked}
    if len(picked) < limit:
        urgent = session.execute(
            select(OpenQuestionModel)
            .where(
                OpenQuestionModel.project_id == project_id,
                OpenQuestionModel.status == QuestionStatus.OPEN.value,
                OpenQuestionModel.priority.in_(_URGENT_PRIORITIES),
            )
            .order_by(OpenQuestionModel.question_id)
        ).scalars()
        ranked = sorted(urgent, key=lambda m: _URGENT_PRIORITIES.index(m.priority))
        for model in ranked:
            if model.question_id not in seen:
                seen.add(model.question_id)
                picked.append(_hint_entry(model, None))
    return picked[:limit]


_URGENT_PRIORITIES = ("critical", "high")


def _hint_entry(model: OpenQuestionModel, relation: str | None) -> dict[str, object]:
    return {
        "question_id": model.question_id,
        "title": model.title,
        "status": model.status,
        "priority": model.priority,
        "relation": relation,
    }
