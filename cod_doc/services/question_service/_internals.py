"""Module-internal helpers — lookup, id allocation, ref shape, audit trail."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, QuestionLinkKind
from cod_doc.infra.models import OpenQuestionModel
from cod_doc.services import activity_service, search_service
from cod_doc.services.validation import ValidationError

from ._types import QuestionNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_QUESTION_ID_RE = re.compile(r"^Q-(\d{3,})$")
_ID_WIDTH = 3

# Форма ссылки проверяется при записи, существование цели — в ``verify``:
# вопрос часто пишут раньше задачи, которая его закроет (та же логика, что у
# ``scenario_link``).
_REF_SHAPES: dict[QuestionLinkKind, re.Pattern[str]] = {
    QuestionLinkKind.TASK: re.compile(r"^[A-Z]{2,5}-\d{3}[A-Z]?$"),
    QuestionLinkKind.ADR: re.compile(r"^ADR-\d{3,}$"),
    QuestionLinkKind.STORY: re.compile(r"^[A-Z]{2,4}-\d{3}$"),
    QuestionLinkKind.SCENARIO: re.compile(r"^SCN-\d{3,}$"),
    QuestionLinkKind.SECTION: re.compile(r"^[^#\s][^#]*#[^#\s]+$"),
    QuestionLinkKind.URL: re.compile(r"^https?://\S+$"),
}
_MAX_REF_LEN = 512


def _require_question(session: Session, project_id: int, question_id: str) -> OpenQuestionModel:
    stmt = select(OpenQuestionModel).where(
        OpenQuestionModel.project_id == project_id,
        OpenQuestionModel.question_id == question_id,
    )
    m = session.execute(stmt).scalar_one_or_none()
    if m is None:
        raise QuestionNotFoundError(question_id)
    return m


def _next_question_id(session: Session, project_id: int) -> str:
    """Allocate ``Q-NNN`` as max+1 within the project (mirrors ``_next_scenario_id``)."""
    stmt = select(OpenQuestionModel.question_id).where(OpenQuestionModel.project_id == project_id)
    highest = 0
    for value in session.execute(stmt).scalars():
        match = _QUESTION_ID_RE.match(value)
        if match is not None:
            highest = max(highest, int(match.group(1)))
    return f"Q-{highest + 1:0{_ID_WIDTH}d}"


def validate_question_id(question_id: str) -> None:
    if not _QUESTION_ID_RE.fullmatch(question_id):
        raise ValidationError(
            "OQ-001",
            f"invalid question_id {question_id!r}: expected 'Q-NNN'",
            question_id=question_id,
        )


def validate_text(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValidationError("OQ-002", f"{field_name} must not be empty", field=field_name)


def split_code_ref(to_ref: str) -> tuple[str, str | None]:
    """``path#fragment`` → (path, fragment); фрагмент — символ или ``L10-L20``."""
    path, sep, fragment = to_ref.partition("#")
    return path, (fragment if sep and fragment else None)


def validate_ref(to_kind: QuestionLinkKind, to_ref: str) -> None:
    """Проверить форму ``to_ref`` для вида ссылки; существование — не здесь."""
    if not to_ref.strip() or len(to_ref) > _MAX_REF_LEN or to_ref != to_ref.strip():
        raise ValidationError("OQ-003", f"invalid ref {to_ref!r}", to_kind=to_kind.value)
    if to_kind is QuestionLinkKind.CODE:
        path, _ = split_code_ref(to_ref)
        posix = PurePosixPath(path)
        if not path or posix.is_absolute() or ".." in posix.parts:
            raise ValidationError(
                "OQ-003",
                f"code ref must be a project-relative path[#symbol|#L10-L20], got {to_ref!r}",
                to_kind=to_kind.value,
            )
        return
    shape = _REF_SHAPES.get(to_kind)
    if shape is not None and not shape.fullmatch(to_ref):
        raise ValidationError(
            "OQ-003",
            f"ref {to_ref!r} does not look like a {to_kind.value} reference",
            to_kind=to_kind.value,
        )


def _diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields}, ensure_ascii=False, default=str)


def record(
    session: Session,
    model: OpenQuestionModel,
    *,
    author: str,
    op: str,
    reason: str | None,
    summary: str,
    payload: dict[str, Any],
) -> None:
    """Revision + activity event + FTS refresh — один вызов на каждую мутацию."""
    session.flush()
    activity_service.write_revision_and_emit_event(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.QUESTION,
        entity_id=model.row_id,
        author=author,
        diff=_diff(op, question_id=model.question_id, **payload),
        reason=reason or op,
        activity_kind=f"question.{op}",
        activity_scope_kind="question",
        activity_scope_id=model.question_id,
        activity_payload=payload,
        activity_summary=summary,
    )
    search_service.index_question(session, model)
    session.flush()
