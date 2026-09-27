"""Answer options of a question — the structured half of «варианты решения».

Positions are stable ids within a question: removing an option leaves a gap
instead of renumbering, so ``resolve --option 2`` keeps meaning the option
that was shown as #2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.infra.models import QuestionOptionModel
from cod_doc.infra.repositories import QuestionOptionRepository
from cod_doc.services import activity_service, search_service

from ._internals import _require_question, audit_kwargs, validate_text
from ._types import QuestionOptionNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import QuestionOption
    from cod_doc.infra.models import OpenQuestionModel


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


def list_options(session: Session, question_row_id: int) -> list[QuestionOption]:
    return QuestionOptionRepository(session).list_for_question(question_row_id)


def _require_option(model: OpenQuestionModel, position: int) -> QuestionOptionModel:
    for opt in model.options:
        if opt.position == position:
            return opt
    raise QuestionOptionNotFoundError(f"{model.question_id} option #{position}")


def add_option(
    session: Session,
    *,
    project_id: int,
    question_id: str,
    title: str,
    author: str,
    body: str | None = None,
    reason: str | None = None,
) -> QuestionOption:
    """Append an option; its position is max+1 (never reuses a removed slot)."""
    validate_text("option title", title)
    model = _require_question(session, project_id, question_id)
    position = max((o.position for o in model.options), default=-1) + 1
    opt = QuestionOptionModel(position=position, title=title, body=body, chosen=False)
    model.options.append(opt)
    model.last_updated = datetime.now(UTC)
    _record(
        session,
        model,
        author=author,
        op="option_added",
        reason=reason,
        summary=f"Question {question_id}: option #{position} {title}",
        payload={"position": position, "title": title},
    )
    return QuestionOptionRepository(session)._to_domain(opt)


def update_option(
    session: Session,
    *,
    project_id: int,
    question_id: str,
    position: int,
    author: str,
    title: str | None = None,
    body: str | None = None,
    reason: str | None = None,
) -> QuestionOption:
    """Patch an option's title/body; ``body=""`` clears it."""
    model = _require_question(session, project_id, question_id)
    opt = _require_option(model, position)
    changed: dict[str, object] = {}
    if title is not None and title != opt.title:
        validate_text("option title", title)
        changed["title"] = opt.title = title
    if body is not None and (body or None) != opt.body:
        changed["body"] = opt.body = body or None
    if changed:
        model.last_updated = datetime.now(UTC)
        _record(
            session,
            model,
            author=author,
            op="option_updated",
            reason=reason,
            summary=f"Question {question_id}: option #{position} updated",
            payload={"position": position, "fields": sorted(changed)},
        )
    return QuestionOptionRepository(session)._to_domain(opt)


def remove_option(
    session: Session,
    *,
    project_id: int,
    question_id: str,
    position: int,
    author: str,
    reason: str | None = None,
) -> None:
    model = _require_question(session, project_id, question_id)
    opt = _require_option(model, position)
    model.options.remove(opt)
    model.last_updated = datetime.now(UTC)
    _record(
        session,
        model,
        author=author,
        op="option_removed",
        reason=reason,
        summary=f"Question {question_id}: option #{position} removed",
        payload={"position": position, "title": opt.title},
    )
