"""Question edges — what a question is about, blocks, or is answered by.

``to_ref`` is validated for shape on write and for existence by
:func:`cod_doc.services.question_service.verify.verify_links`: a question is
often written before the task that will answer it exists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import (
    OpenQuestion,
    QuestionLink,
    QuestionLinkKind,
    QuestionRelation,
    QuestionStatus,
)
from cod_doc.infra.models import OpenQuestionModel, QuestionLinkModel
from cod_doc.infra.repositories import OpenQuestionRepository, QuestionLinkRepository

from ._internals import _require_question, record, validate_ref

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
    payload = {"to_kind": to_kind.value, "to_ref": to_ref, "relation": relation.value}
    record(
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
    record(
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
