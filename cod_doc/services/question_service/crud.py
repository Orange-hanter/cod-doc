"""Question CRUD and lifecycle — create, read, update, resolve, drop, reopen.

Every mutation writes a revision and emits an activity event in one atomic
call (ADO-040) and refreshes the question's FTS row. Options and links are
revisioned under their parent ``EntityKind.QUESTION``, as scenario steps are
under their scenario.

None of this touches ``document`` / ``section``: a question is never
projected into markdown.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import (
    OpenQuestion,
    Priority,
    QuestionLinkKind,
    QuestionRelation,
    QuestionStatus,
)
from cod_doc.infra.models import OpenQuestionModel, QuestionLinkModel, QuestionOptionModel
from cod_doc.infra.repositories import OpenQuestionRepository
from cod_doc.services import activity_service, search_service

from ._internals import (
    _next_question_id,
    _require_question,
    audit_kwargs,
    validate_question_id,
    validate_ref,
    validate_text,
)
from ._types import QuestionAlreadyExistsError, QuestionOptionNotFoundError, QuestionStateError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

# Порядок выдачи: сначала открытые, внутри — по срочности.
_STATUS_ORDER = {s: i for i, s in enumerate(QuestionStatus)}
_PRIORITY_ORDER = {p: i for i, p in enumerate(Priority)}


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


def create(
    session: Session,
    *,
    project_id: int,
    title: str,
    question: str,
    author: str,
    context: str | None = None,
    priority: Priority = Priority.MEDIUM,
    owner: str | None = None,
    options: Sequence[tuple[str, str | None]] = (),
    question_id: str | None = None,
    source_doc_key: str | None = None,
    reason: str | None = None,
) -> OpenQuestion:
    """Persist a question (+ optional ``(title, body)`` options) in status ``open``."""
    validate_text("title", title)
    validate_text("question", question)
    for opt_title, _ in options:
        validate_text("option title", opt_title)

    repo = OpenQuestionRepository(session)
    qid = question_id or _next_question_id(session, project_id)
    validate_question_id(qid)
    if repo.get_by_question_id(project_id, qid) is not None:
        raise QuestionAlreadyExistsError(qid)

    now = datetime.now(UTC)
    created = repo.add(
        OpenQuestion(
            project_id=project_id,
            question_id=qid,
            title=title,
            question=question,
            context=context,
            priority=priority,
            owner=owner,
            author=author,
            source_doc_key=source_doc_key,
            created=now,
            last_updated=now,
        )
    )
    assert created.row_id is not None
    for i, (opt_title, opt_body) in enumerate(options):
        session.add(
            QuestionOptionModel(
                question_row_id=created.row_id,
                position=i,
                title=opt_title,
                body=opt_body,
                chosen=False,
            )
        )

    model = _require_question(session, project_id, qid)
    _record(
        session,
        model,
        author=author,
        op="created",
        reason=reason,
        summary=f"Question {qid} opened: {title}",
        payload={
            "priority": priority.value,
            "owner": owner,
            "option_count": len(options),
            "source_doc_key": source_doc_key,
        },
    )
    fresh = get(session, project_id, qid)
    assert fresh is not None
    return fresh


def get(session: Session, project_id: int, question_id: str) -> OpenQuestion | None:
    return OpenQuestionRepository(session).get_by_question_id(project_id, question_id)


def list_for_project(
    session: Session,
    project_id: int,
    *,
    status: QuestionStatus | None = None,
    owner: str | None = None,
    priority: Priority | None = None,
    linked_to: tuple[QuestionLinkKind, str] | None = None,
) -> list[OpenQuestion]:
    """Questions of the project; open first, then by priority and id."""
    stmt = select(OpenQuestionModel).where(OpenQuestionModel.project_id == project_id)
    if status is not None:
        stmt = stmt.where(OpenQuestionModel.status == status.value)
    if owner is not None:
        stmt = stmt.where(OpenQuestionModel.owner == owner)
    if priority is not None:
        stmt = stmt.where(OpenQuestionModel.priority == priority.value)
    if linked_to is not None:
        kind, ref = linked_to
        stmt = stmt.where(
            OpenQuestionModel.row_id.in_(
                select(QuestionLinkModel.question_row_id).where(
                    QuestionLinkModel.to_kind == kind.value,
                    QuestionLinkModel.to_ref == ref,
                )
            )
        )
    repo = OpenQuestionRepository(session)
    found = [repo._to_domain(m) for m in session.execute(stmt).scalars()]
    found.sort(
        key=lambda q: (
            _STATUS_ORDER[q.status],
            _PRIORITY_ORDER[q.priority],
            q.question_id,
        )
    )
    return found


def update(
    session: Session,
    *,
    project_id: int,
    question_id: str,
    author: str,
    title: str | None = None,
    question: str | None = None,
    context: str | None = None,
    priority: Priority | None = None,
    owner: str | None = None,
    reason: str | None = None,
) -> OpenQuestion:
    """Patch the given fields; ``owner=""`` / ``context=""`` clear the value."""
    model = _require_question(session, project_id, question_id)
    changed: dict[str, object] = {}

    if title is not None and title != model.title:
        validate_text("title", title)
        changed["title"] = model.title = title
    if question is not None and question != model.question:
        validate_text("question", question)
        changed["question"] = model.question = question
    if context is not None and (context or None) != model.context:
        changed["context"] = model.context = context or None
    if priority is not None and priority.value != model.priority:
        changed["priority"] = model.priority = priority.value
    if owner is not None and (owner or None) != model.owner:
        changed["owner"] = model.owner = owner or None

    if changed:
        model.last_updated = datetime.now(UTC)
        _record(
            session,
            model,
            author=author,
            op="updated",
            reason=reason,
            summary=f"Question {question_id} updated",
            payload={"fields": sorted(changed), **changed},
        )
    updated = get(session, project_id, question_id)
    assert updated is not None
    return updated


def _mark_chosen(model: OpenQuestionModel, position: int | None) -> None:
    if position is not None and all(o.position != position for o in model.options):
        raise QuestionOptionNotFoundError(f"{model.question_id} option #{position}")
    for opt in model.options:
        opt.chosen = opt.position == position


def resolve(
    session: Session,
    *,
    project_id: int,
    question_id: str,
    author: str,
    resolution: str | None = None,
    by_adr: str | None = None,
    chosen_option: int | None = None,
    reason: str | None = None,
) -> OpenQuestion:
    """Close an open question with an answer.

    At least one of ``resolution`` / ``by_adr`` / ``chosen_option`` is
    required — a question closed without any answer is ``drop``, not
    ``resolve``. ``by_adr`` also records a ``resolved_by`` edge to the ADR so
    the answer is reachable from the link list and ``question list
    --linked-to adr:ADR-NNN``.
    """
    model = _require_question(session, project_id, question_id)
    if model.status != QuestionStatus.OPEN.value:
        raise QuestionStateError(f"{question_id} is {model.status}; reopen it first")
    if not (resolution and resolution.strip()) and by_adr is None and chosen_option is None:
        raise QuestionStateError("resolve needs a resolution text, an ADR or a chosen option")
    if by_adr is not None:
        validate_ref(QuestionLinkKind.ADR, by_adr)

    _mark_chosen(model, chosen_option)
    now = datetime.now(UTC)
    model.status = QuestionStatus.RESOLVED.value
    model.resolution = resolution or None
    model.resolved_by_adr = by_adr
    model.resolved_at = now
    model.last_updated = now
    if by_adr is not None and not any(
        link.to_kind == QuestionLinkKind.ADR.value
        and link.to_ref == by_adr
        and link.relation == QuestionRelation.RESOLVED_BY.value
        for link in model.links
    ):
        model.links.append(
            QuestionLinkModel(
                to_kind=QuestionLinkKind.ADR.value,
                to_ref=by_adr,
                relation=QuestionRelation.RESOLVED_BY.value,
            )
        )

    _record(
        session,
        model,
        author=author,
        op="resolved",
        reason=reason,
        summary=f"Question {question_id} resolved" + (f" by {by_adr}" if by_adr else ""),
        payload={"by_adr": by_adr, "chosen_option": chosen_option, "resolution": resolution},
    )
    resolved = get(session, project_id, question_id)
    assert resolved is not None
    return resolved


def drop(
    session: Session,
    *,
    project_id: int,
    question_id: str,
    author: str,
    resolution: str,
    reason: str | None = None,
) -> OpenQuestion:
    """Close a question without an answer; ``resolution`` says why it was dropped."""
    validate_text("resolution", resolution)
    model = _require_question(session, project_id, question_id)
    if model.status != QuestionStatus.OPEN.value:
        raise QuestionStateError(f"{question_id} is {model.status}; reopen it first")

    now = datetime.now(UTC)
    model.status = QuestionStatus.DROPPED.value
    model.resolution = resolution
    model.resolved_at = now
    model.last_updated = now
    _record(
        session,
        model,
        author=author,
        op="dropped",
        reason=reason,
        summary=f"Question {question_id} dropped",
        payload={"resolution": resolution},
    )
    dropped = get(session, project_id, question_id)
    assert dropped is not None
    return dropped


def reopen(
    session: Session,
    *,
    project_id: int,
    question_id: str,
    author: str,
    reason: str | None = None,
) -> OpenQuestion:
    """Return a resolved/dropped question to ``open``.

    The previous answer is cleared from the row — it stays in the revision
    history, and a reopened question with a stale answer next to it reads as
    still answered. ``resolved_by`` edges are kept: they are part of the
    discussion trail.
    """
    model = _require_question(session, project_id, question_id)
    if model.status == QuestionStatus.OPEN.value:
        raise QuestionStateError(f"{question_id} is already open")

    old = model.status
    model.status = QuestionStatus.OPEN.value
    model.resolution = None
    model.resolved_by_adr = None
    model.resolved_at = None
    model.last_updated = datetime.now(UTC)
    _mark_chosen(model, None)
    _record(
        session,
        model,
        author=author,
        op="reopened",
        reason=reason,
        summary=f"Question {question_id} reopened",
        payload={"old_status": old},
    )
    reopened = get(session, project_id, question_id)
    assert reopened is not None
    return reopened
